# utils
import os
import sys
import random
import time
import logging
import math
import numpy
import argparse

# torch 
import torch
import torch.nn.functional as F
import torch.nn as nn
import torch.optim as optim
import torch.distributed as dist
import torchvision
from torchvision import datasets, transforms, models
import torch.multiprocessing as mp

# custom
from core.net import resnet50
from core.data import get_dataset, partition_dataloader
from core.utils import AverageMeter, accuracy, create_chart

def log_info(*value):
    if dist.is_initialized() :
        logging.info(f'[Rank-{dist.get_rank()}] {" ".join(map(str, value))}')
    else :
        logging.info(' '.join(map(str, value)))

def console_progress(message, new_line = False):
    if new_line :
        sys.stdout.write('\033[1K\r%s\n' % (message))
    else:
        sys.stdout.write('\033[1K\r%s' % (message))

def reduce_average_gradients(model, group = None):
    size = float(dist.get_world_size())
    for param in model.parameters():
        handle = dist.all_reduce(param.grad.data, op=dist.ReduceOp.SUM, group=group)
        param.grad.data /= size

    return handle 

def save_gradinets(model):
    store = []
    for param in model.parameters():
        store.append(param.grad.data.clone().detach())
    
    return store

def average_gradients(model, gradients):
    for idx, param in enumerate(model.parameters()):
        param.grad.data += gradients[idx]
        param.grad.data /= 2

def std_mean_loss(loss):
    with torch.no_grad():
        cur_losses = torch.Tensor([ loss for _ in range(dist.get_world_size()) ]).cuda()
        cur_losses = list(cur_losses.chunk(dist.get_world_size()))
        all_losses = list(torch.empty([dist.get_world_size()], dtype=torch.float32).cuda().chunk(dist.get_world_size()))
        dist.all_to_all(all_losses, cur_losses)
        std_loss, _ = torch.std_mean(torch.Tensor(all_losses))
        return (std_loss, all_losses)
    
def train(share, seed, epoch, model, dataset, criterion, optimizer, scheduler, args, batch_size = 384):
    start_time = time.time()
    seed += epoch
    torch.manual_seed(seed)
    train_set, batch_size = partition_dataloader(batch_size=batch_size, seed=seed, dataset=dataset) #imagenet 384
    num_step = len(train_set)

    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()
    std_loss = AverageMeter()

    communication_count = 0
    pre_std_loss = 0
    model.train()
    str_losses = ""

    send_buffer = []
    recv_buffer = []

    for step, (data, target) in enumerate(train_set):
        start_step_time = time.time()
        data, target = data.cuda(non_blocking=True), target.cuda(non_blocking=True)
        
        # train
        output = model(data)
        loss = criterion(output, target)

        optimizer.zero_grad()
        loss.backward()

        # calc state
        aucc1, aucc5 = accuracy(output, target, topk=(1, 5))
        losses.update(loss.item(), target.size(0))
        top1.update(aucc1[0], target.size(0))
        top5.update(aucc5[0], target.size(0))


        if args.train_type == "minibatch":
            reduce_average_gradients(model)

        elif  args.train_type == "local":
            if step > 0 and args.k > 0 and step % args.k == 0:
                reduce_average_gradients(model)
        else:
            if args.k > 0 and step % args.k == 0:
                # 데이터를 복사한다.
                # 데이터를 공유한다 aync 로
                # 이전에 받은 데이터로 현재의 로스에 합쳐서 학습 진행 (d*4 + cur) / 5
                recv_buffer = send_buffer
                send_buffer = []
                
                if step > 0:
                    idx = 0
                    for param in model.parameters():
                        data = param.grad.data.clone().detach()
                        dist.all_reduce(data, op=dist.ReduceOp.SUM, async_op=True)
                        send_buffer.append(data)
                        param.grad.data = (param.grad.data + (recv_buffer[idx] * args.world))/5
                        idx += 1
                else:
                    for param in model.parameters():
                        data = param.grad.data.clone().detach()
                        dist.all_reduce(data, op=dist.ReduceOp.SUM, async_op=True)
                        send_buffer.append(data)
            
            

        optimizer.step()

        message = 'step time {:.4f}, epoch {:3}, step {:3}/{:3}, loss {:.8f}, auc@1 {:3.2f}, auc@5 {:3.2f}, share {}, std_loss {}, {}'.format(
            time.time() - start_step_time, epoch+1, step+1, num_step, losses.avg, top1.avg, top5.avg, communication_count, std_loss.avg, str_losses
        )

        console_progress( message)
        #log_info(message)

    with torch.no_grad():
        total_state = torch.tensor([losses.sum, losses.count, top1.sum, top1.count, top5.sum, top5.count]).cuda()
        
        if args.world >= 2:
            dist.all_reduce(total_state, op=dist.ReduceOp.SUM)

        losses = total_state[0] / total_state[1]
        top1 = total_state[2] / total_state[3]
        top5 = total_state[4] / total_state[5]

        train_time = time.time() - start_time
        message = 'train time {:.4f}, epoch {:3}, step {:3}, loss {:.8f}, auc@1 {:3.2f}, auc@5 {:3.2f}, share {}, std_loss {}, {}'.format(
            train_time, epoch+1, num_step, losses, top1, top5, communication_count, std_loss.avg, str_losses
        )
        console_progress(message, new_line = True)
        log_info(message)
    return communication_count

def val(share, seed, epoch, model, dataset, criterion, batch_size = 384):
    start_time = time.time()
    seed += epoch
    torch.manual_seed(seed)
    #val_set, batch_size = partition_dataloader(batch_size=batch_size, seed=seed, dataset=dataset) #imagenet 384
    num_step = len(dataset)

    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()

    communication_count = 0
    pre_std_loss = 0
    model.eval()
    with torch.no_grad():
        for step, (data, target) in enumerate(dataset):
            start_step_time = time.time()
            data, target = data.cuda(non_blocking=True), target.cuda(non_blocking=True)
            output = model(data)
            loss = criterion(output, target)

            # calc state
            aucc1, aucc5 = accuracy(output, target, topk=(1, 5))
            losses.update(loss.item(), target.size(0))
            top1.update(aucc1[0], target.size(0))
            top5.update(aucc5[0], target.size(0))

            message = 'val time {:.4f}, epoch {:3}, step {:3}/{:3}, loss {:.8f}, auc@1 {:3.2f}, auc@5 {:3.2f}'.format(
                time.time() - start_step_time, epoch+1, step+1, num_step, losses.avg, top1.avg, top5.avg
            )

            console_progress(message)

        #total_state = torch.tensor([losses.sum, losses.count, top1.sum, top1.count, top5.sum, top5.count]).cuda()
        #dist.all_reduce(total_state, op=dist.ReduceOp.SUM)

        #losses = total_state[0] / total_state[1]
        #top1 = total_state[2] / total_state[3]
        #top5 = total_state[4] / total_state[5]

        train_time = time.time() - start_time
        message = 'val time {:.4f}, epoch {:3}, step {:3}, loss {:.8f}, auc@1 {:3.2f}, auc@5 {:3.2f}'.format(
            train_time, epoch+1, num_step, losses.avg, top1.avg, top5.avg
        )
        console_progress(message, new_line = True)
        log_info(message)

def init_process(args):
    group = None
    seed = 7548
    lr = args.lr
    os.environ['NCCL_ALGO'] = 'Ring'

    print('args :', args)

    rank0_time = [args.current_time]
    if args.world >= 2:
        dist.init_process_group(init_method=f'tcp://{args.ip}', backend='nccl', rank=args.rank, world_size=args.world)
        dist.broadcast_object_list(rank0_time, 0)

    formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')
    logger = logging.getLogger('')
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(f'logs/{rank0_time[0]}/{args.train_type}_{args.rank}.log')
    #sh = logging.StreamHandler(sys.stdout)
    fh.setFormatter(formatter)
    #sh.setFormatter(formatter)
    logger.addHandler(fh)
    #logger.addHandler(sh)

    ##############################################
    #train
    ##############################################
    console_progress(f'### start train {args.train_type} ###', new_line = True)
    log_info('args :', args)


    # model = models.resnet50(num_classes=100).cuda()
    #model = MNISTResNet().cuda()
    model = resnet50().cuda()

    train_set = get_dataset(args.dataset, train = True)
    val_set = get_dataset(args.dataset, train = False)
    var_loader= torch.utils.data.DataLoader(val_set, batch_size = int(args.batch/args.world), shuffle=True,  num_workers=5, pin_memory=True)

    criterion = nn.CrossEntropyLoss().cuda()
    #optimizer = optim.AdamW(model.parameters(), lr = args.lr)
    optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=args.momentum, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=60, gamma=0.9)

    total_train_start_time = time.time()
    only_train_time_sum = 0
    communication_count = 0
    for epoch in range(args.epochs):
        train_time = time.time()
        communication_count += train(args.train_type, seed, epoch, model, train_set, criterion, optimizer, scheduler, args, batch_size= args.batch)
        only_train_time_sum += (time.time() - train_time)
        val(args.train_type, seed, epoch, model, var_loader, criterion, batch_size= args.batch)
        scheduler.step()    
    log_info('finish, train+val {:0.5f}, train {:0.5f}, reduce_count {:3}'.format(time.time() - total_train_start_time, only_train_time_sum, communication_count))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Dist training')
    parser.add_argument('rank', type=int, default=0)
    parser.add_argument('world', type=int, default=4)
    parser.add_argument('--batch', type=int, default=256) # 384
    parser.add_argument('--epochs', type=int, default=300)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--dataset', default='cifar100')
    parser.add_argument('--train_type', default='none')
    parser.add_argument('--momentum', type=float, default=0.9)
    parser.add_argument('--weight_decay', type=float, default=0.05)
    parser.add_argument('--k', type=int, default=0)
    parser.add_argument('--ip', default='192.168.1.1:7548')
    args = parser.parse_args()
    
    args.current_time = time.strftime('%Y%m%d_%H%M%S', time.localtime(time.time()))
    base_log_path = f"logs/{args.current_time}/"

    if args.rank == 0:
        os.mkdir(base_log_path)

    mp.set_start_method("spawn")

    args.train_type = "minibatch"
    p = mp.Process(target=init_process, args=(args,))
    p.start()
    p.join()

    if args.world >= 2:
        args.train_type = "local"
        p = mp.Process(target=init_process, args=(args,))
        p.start()
        p.join()

        args.train_type = "proposal"
        p = mp.Process(target=init_process, args=(args,))
        p.start()
        p.join()
    
    if args.rank == 0:
        create_chart(base_log_path)
    
    # 공유 메모리 할당
    # model = MNISTResNet()
    # shm_model = []
    # for param in model.parameters():
    #     shm_model.append(param.data.share_memory_())

    # 학습 프로세스 시작
    print("finish")

