# 석사논문 분산 딥러닝 실험 코드
- 논문명 : 분산 딥러닝을 위해 Loss로 통신 시점을 결정하는 비동기 SGD
- 분산 딥러닝에서 노드 간 파라미터 통신을 수행할때 가 노드에서 학습 중인 모델들의 Loss 상태에 따라 통신 여부를 결정하는 결정하는 방식이 모델의 성능에 어떠한 영향을 미치는지를 실험함

# 초록
> 딥러닝 기술의 발전으로 학습데이터와 딥러닝 모델의 규모가 커짐에 따라 딥러닝 모델의 학습 시간이 급속도로 증가하는 문제가 발생하였다. 이를 해결하기 위하여 다수의 하드웨어 자원에 학습 연산을 분산하여 학습 시간을 단축하는 분산 딥러닝 기술이 주목을 받아 다양한 연구가 진행되고 있다.
본 연구에서는 학습 데이터를 분산하여 학습 노드 간 집단 통신을 수행하는 환경에서 딥러닝 모델의 학습 속도 및 성능 향상을 위하여 Loss로 파라미터 통신 시점을 결정하는 비동기 SGD 기법을 제안한다. 본 제안은 다수의 노드에서 학습 중인 모델들 간에 학습 파라미터를 공유하기 위하여 통신 시점을 결정할 때 기존 기법들에서 사용하는 고정된 하이터 파라미터를 대체하기 위하여 현재의 모델의 학습 상태를 보고 판단하는 기법으로서, 현재 학습 중인 모델들의 Loss의 표준 편차를 계산하고 이전 학습보다 표준 편차가 증가할 때 비동기 방식으로 파라미터 통신을 수행한다. 비동기 방식으로 통신할 때 네트워크 속도에 의해서 학습 연산의 병목현상을 줄일수 있는 장점이 있으나 이전 시점의 데이터로 학습한 Gradient가 현재 학습에 반영되어 모델의 수렴을 방해하는 Stale Gradient Problem이 발생하게 되는데, 이를 해결하기 위하여 Lookahead 기법에서 착안하여 현재 시점의 Gradient와 이전 시점의 Gradient의 차이에 따라 현재의 Gradient를 보정하는 기법을 적용하는 연구를 수행하였다.
본 제안의 검증을 위하여 기존 기법들과 비교 실험을 수행하였으며 그 결과로 모델의 성능을 유지하면서 학습 시간이 단축되는 것을 확인하였다.

# 실험 결과 및 논문
- 논문명 : [분산 딥러닝을 위해 Loss로 통신 시점을 결정하는 비동기 SGD, 국내석사학위논문 경희대학교 대학원, 2021](http://www.riss.kr/search/detail/DetailView.do?p_mat_type=be54d9b8bc7cdb09&control_no=d1c4c88daebce394ffe0bdc3ef48d419)
