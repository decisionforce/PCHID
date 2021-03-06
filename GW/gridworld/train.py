import matplotlib
import imageio,os
from copy import deepcopy
matplotlib.use('Agg')
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '3'
file_name = '_size8_10obs_5000dom'
image_size = 8
domains_num = 5000
K=10
class conf():
    def __init__(self,
                 datafile = 'dataset/gridworld_RL_{0}x{1}'.format(image_size,image_size)+file_name+'.npz',
                 imsize= image_size,
                 lr = 0.005,
                 epochs = 30,
                 k = K,
                 l_i = 2,
                 l_h = 150,
                 l_q = 10,
                 batch_size = 128,
                 domains_num = domains_num,
                 algorithm = 'PCHID_VIN',
                 experiment = 'pchid_vin'):
        self.domains_num = domains_num
        self.l_i = l_i
        self.l_h = l_h
        self.imsize= imsize
        self.lr =lr
        self.epochs = epochs
        self.k = k
        self.l_q = l_q
        self.batch_size = batch_size
        self.datafile = datafile
        self.algorithm = algorithm
        self.experiment = experiment

from dataset.dataset import *
from utility.utils import *
import matplotlib.pyplot as plt
from domains.gridworld import *
import random
from collections import deque
import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F
import numpy as np
from torch.nn.parameter import Parameter


#file_n = '16x16_60_5000'
image_size = image_size
config = conf()
transform = None

trainset = GridworldData(
        config.datafile, imsize=config.imsize, train=True, transform=transform)
print(trainset.images.shape)
X_train = trainset.images
X_set = [X_train[0]]
for i in range(1,len(X_train)):
    if False in (set((X_train[i] ==X_set[-1]).flatten())):
        X_set.append(X_train[i])
print("X_train.shape",X_train.shape)
print("X_set.shape",np.shape(X_set))

class GW_env():
    def __init__(self,X):
        self.R ,self.goal = self.X2R(X)
        self.G = gridworld(1-X[0], self.goal[0], self.goal[1])
        self.actions = np.asarray([[-1, 0], [1, 0], [0, 1], [0, -1],
                              [-1, 1], [-1, -1], [1, 1], [1, -1]])
    def X2R(self,X):
        goal = [np.argmax(X[1])//config.imsize,np.argmax(X[1])%config.imsize]
        G = gridworld(1-X[0], goal[0], goal[1])
        R = X[1] - (1-X[0])*0.02 - 2 * X[0]
        return R,goal

    def reset(self):
        self.states_xy, self.states_one_hot = sample_trajectory(self.G, 1)
        if len(self.states_xy[0])>0:
            self.s = self.states_xy[0][0]
            self.bestlen = len(self.states_xy[0])
            #print(self.bestlen)
        else:
            self.reset()


    def step(self,a):
        s_0 = self.s.copy()
        #print('takeastep')
        self.s = self.s + self.actions[a]
        if self.s[0]>=config.imsize-1:
            self.s[0] = config.imsize-1
        if self.s[0]<=0:
            self.s[0] = 0
        if self.s[1]>=config.imsize-1:
            self.s[1] = config.imsize-1
        if self.s[1]<=0:
            self.s[1] = 0
        reward = self.reward()
        ifdone = self.ifdone()
        #s = self.s[:]
        if X[0][tuple(self.s)]==1:# target s is an obstacle
            self.s = s_0.copy()
        return self.s,reward,ifdone,0

    def reward(self):
        if self.R[int(self.s[0]),int(self.s[1])] == -2:
            return 0
        else:
            return self.R[int(self.s[0]),int(self.s[1])]

    def ifdone(self):
        if self.s[0]==self.goal[0] and self.s[1]==self.goal[1]:
            return True
        else:
            return False

use_GPU = torch.cuda.is_available()

# Hyper Parameters
BATCH_SIZE = 32
LR = 0.001 # learning rate
EPSILON = 1 # greedy policy
GAMMA = 0.9 # reward discount
TARGET_REPLACE_ITER = 100 # target update frequency
MEMORY_CAPACITY = 30000
EPISODES = 3000

#env = env.unwrapped
N_ACTIONS = 8
N_STATES = 2 +image_size*image_size

class VIN(nn.Module):
    def __init__(self, config):
        super(VIN, self).__init__()
        self.config = config
        self.h = nn.Conv2d(
            in_channels=1,
            out_channels=config.l_h,
            kernel_size=(3, 3),
            stride=1,
            padding=1,
            bias=True)
        self.r = nn.Conv2d(
            in_channels=config.l_h,
            out_channels=1,
            kernel_size=(1, 1),
            stride=1,
            padding=0,
            bias=False)
        self.q = nn.Conv2d(
            in_channels=1,
            out_channels=config.l_q,
            kernel_size=(3, 3),
            stride=1,
            padding=1,
            bias=False)
        self.fc = nn.Linear(in_features=config.l_q, out_features=8, bias=False)
        self.w = Parameter(
            torch.zeros(config.l_q, 1, 3, 3), requires_grad=True)
        self.sm = nn.Softmax(dim=1)

    def forward(self,S,config):
        #print(S.shape)
        S = S.reshape([-1,N_STATES])
        X = S[:,:config.imsize**2].reshape([-1,1,config.imsize,config.imsize])
        S1 = S[:,config.imsize**2:config.imsize**2+1].long().squeeze(1)
        #print(S1.shape)
        S2 = S[:,config.imsize**2+1:].long().squeeze(1)
        #print(S2.shape)
        h = self.h(X)
        r = self.r(h)
        q = self.q(r)
        v, _ = torch.max(q, dim=1, keepdim=True)
        for i in range(0, config.k - 1):
            q = F.conv2d(
                torch.cat([r, v], 1),
                torch.cat([self.q.weight, self.w], 1),
                stride=1,
                padding=1)
            v, _ = torch.max(q, dim=1, keepdim=True)

        q = F.conv2d(
            torch.cat([r, v], 1),
            torch.cat([self.q.weight, self.w], 1),
            stride=1,
            padding=1)
        #print(q.shape)
        #print(self.q.weight.shape)
        slice_s1 = S1.long().expand(config.imsize, 1, config.l_q, q.size(0))
        slice_s1 = slice_s1.permute(3, 2, 1, 0)
        q_out = q.gather(2, slice_s1).squeeze(2)


        slice_s2 = S2.long().expand(1, config.l_q, q.size(0))
        slice_s2 = slice_s2.permute(2, 1, 0)
        q_out = q_out.gather(2, slice_s2).squeeze(2)



        logits = self.fc(q_out)
        return logits#, self.sm(logits)

class Net(nn.Module):
    def __init__(self,config):
        super(Net,self).__init__()
        self.fc1 = nn.Linear(N_STATES,10)
        self.fc1.weight.data.normal_(0,0.1) # initialization
        self.out = nn.Linear(10,N_ACTIONS)
        self.out.weight.data.normal_(0,0.1) # initialization
    def forward(self,x,config):
        x = self.fc1(x)
        x = F.relu(x)
        actions_value = self.out(x)
        return actions_value

class DQN(object):
    def __init__(self):
        self.eval_net = VIN(config).cuda()
        self.target_net = VIN(config).cuda()
        self.learn_step_counter = 0 # for target updating
        self.memory_counter = 0 # for storing memory
        self.memory = np.zeros((MEMORY_CAPACITY,N_STATES*2+2)) # initailize memory
        self.optimizer = torch.optim.Adam(self.eval_net.parameters(),lr=LR)
        self.loss_func = nn.MSELoss()
        self.epsilon = EPSILON
        self.epsilon_decay = 1/5e6

    def choose_action(self,x):
        #x = Variable(torch.unsqueeze(torch.FloatTensor(x),0))
        #print(x.shape)
        if self.epsilon>0.2:
            self.epsilon-=self.epsilon_decay
        if np.random.uniform() > self.epsilon: # greedy
            #print(torch.cat([R.flatten(),torch.as_tensor(x).float()],0).shape)
            actions_value = self.eval_net.forward(x.cuda(),config)
            #print(actions_value.shape)
            action = torch.max(actions_value,1)[1].data.cpu().numpy()[0]
            #print(action)
        else:
            action = np.random.randint(0,N_ACTIONS)
        return action

    def store_transition(self,s,a,r,s_):
        transition = np.hstack((s,[a,r],s_))
        # replace the old memory with new memory
        index = self.memory_counter % MEMORY_CAPACITY
        self.memory[index,:] = transition
        self.memory_counter += 1

    def learn(self):
        # target net update
        if self.learn_step_counter % TARGET_REPLACE_ITER == 0:
            self.target_net.load_state_dict(self.eval_net.state_dict())
            #print('target net updated')
        sample_index = np.random.choice(MEMORY_CAPACITY,BATCH_SIZE)
        b_memory = self.memory[sample_index,:]
        b_s = Variable(torch.FloatTensor(b_memory[:,:N_STATES]).cuda())
        b_a = Variable(torch.LongTensor(b_memory[:,N_STATES:N_STATES+1].astype(int)).cuda())
        b_r = Variable(torch.FloatTensor(b_memory[:,N_STATES+1:N_STATES+2]).cuda())
        b_s_ = Variable(torch.FloatTensor(b_memory[:,-N_STATES:]).cuda())
        #print('shapeba:',b_a.shape)
        #print('shapebs:',b_s.shape)
        #print(self.eval_net(b_s,config).shape)
        q_eval = self.eval_net(b_s.cuda(),config).gather(1,b_a)
        #print(q_eval.shape)
        q_next = self.target_net(b_s_.cuda(),config).detach()
        #print(q_next.shape)
        q_target = b_r + GAMMA * q_next.max(1)[0].reshape([BATCH_SIZE,1])
        #print(q_next.max(1))
        #print((GAMMA*q_next.max(1)[0]).shape)
        loss = self.loss_func(q_eval.cuda(),q_target.cuda())

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
    def learn_pchid(self,batch_size,step_num):
        if self.learn_step_counter % TARGET_REPLACE_ITER == 0:
            self.target_net.load_state_dict(self.eval_net.state_dict())
            #print('target net updated')
        #def compute_cross_ent_error(batch_size,step_num):
        if ier_buffer.lenth(step_num)==0:
            #print('len0',step_num)
            return None
        if batch_size>ier_buffer.lenth(step_num):
            #print('size bugou')
            return None
        state, action= ier_buffer.sample(batch_size,step_num)
        state          = torch.FloatTensor(state).cuda()
        action_target  = torch.LongTensor(action).cuda()
        action_pred    = self.target_net(state,config)#.max(1)[1]


        #print('tat',action_target.shape,action_target)
        #print('prd',action_pred.shape,action_pred)
        loss_func = nn.CrossEntropyLoss()
        loss = loss_func(action_pred,action_target)
        optimizer_imitation.zero_grad()
        loss.backward()
        optimizer_imitation.step()
        #print('CE updated')
        return loss



class ReplayBuffer_imitation(object):
    def __init__(self, capacity):
        self.buffer = {'1step':deque(maxlen=capacity)}
        self.capacity = capacity
    def push(self, state, action, step_num):
        try:
            self.buffer[step_num]
        except:
            self.buffer[step_num] = deque(maxlen=self.capacity)
        self.buffer[step_num].append((state, action))


    def sample(self, batch_size,step_num):
        state, action= zip(*random.sample(self.buffer[step_num], batch_size))
        return np.stack(state), action

    def lenth(self,step_num):
        try:
            self.buffer[step_num]
        except:
            return 0
        return len(self.buffer[step_num])

    def __len__(self,step_num):
        try:
            self.buffer[step_num]
        except:
            return 0
        return len(self.buffer[step_num])

def test_isvalid_multistep(step_lenth, state_start, environment_start):
    if step_lenth == 1:
        return True
    env_tim = deepcopy(environment_start)
    state_tim = torch.as_tensor(deepcopy(state_start))
    for step_i in range(step_lenth-1):
        a = dqn.eval_net(state_tim.cuda(),config).detach()
        a = torch.max(a,1)[1].data.cpu().numpy()[0]
        next_state_tim,r_tim,done_tim,info_tim = env_tim.step(a)
        next_state_tim = torch.cat([torch.as_tensor(env_tim.R.flatten()),torch.as_tensor(next_state_tim).float()],0)
        next_state_tim[np.where(env_tim.R.flatten()>9)[0][0]] = -0.02
        next_state_tim[np.where(state_/tim.flatten()[:-2]>9)[0][0]] = 9.98

        if next_state_tim.numpy()[-2]*8 + next_state_tim.numpy()[-1] == np.where(next_state_tim.numpy()[:-2]>9)[0][0]:
            return False
        state_tim = next_state_tim
    return True