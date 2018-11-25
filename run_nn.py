import os
import sys
import argparse
import re
import glob
import datetime
import time
import numpy as np
import torch
import nnModules
from torch.utils.data import DataLoader
import torch.optim as optim
from torch.optim.lr_scheduler import MultiStepLR
from dataset_torch_3 import DenoisingDataset
from torch.nn.modules.loss import _Loss
from libs import pytorch_msssim

# Params
parser = argparse.ArgumentParser(description='PyTorch DnCNN')
parser.add_argument('--model', default='DnCNN', type=str, help='Model type (default: DnCNN)')
parser.add_argument('--batch_size', default=32, type=int, help='batch size')
parser.add_argument('--train_data', default='datasets/train/dataset_96', type=str, help='path to the train data (default: '+'datasets/train/dataset_96'+')')
parser.add_argument('--epoch', default=512, type=int, help='number of train epoches')
parser.add_argument('--lr', default=1e-3, type=float, help='initial learning rate for Adam')
parser.add_argument('--expname', default='notset', type=str, help='Experiment name used to save and/or load results and models (default autogenerated from time+CLI)')
parser.add_argument('--result_dir', default='results/train', type=str, help='Directory where results are stored (default: results/train)')
parser.add_argument('--models_dir', default='models', type=str, help='Directory where models are saved/loaded (default: models)')
parser.add_argument('--depth', default=22, type=int, help='Number of layers (default: 22)')
parser.add_argument('--cuda_device', default=0, type=int, help='Device number (default: 0, typically 0-3)')
parser.add_argument('--n_channels', default=64, type=int, help='Number of channels (default: 64)')
parser.add_argument('--find_noise', default=True, type=bool, help='Model noise (True) or clean image (False)')
parser.add_argument('--kernel_size', default=3, type=int, help='Kernel size')
args = parser.parse_args()

# memory eg:
# 1996: res48x48 bs27 = 1932/1996 5260s
# 11172 d22 res96
#   bs12 = 2335
#   bs57 = 8883
#   bs71 = 10813
#   bs72 = 10941
# python3 run_nn.py --batch_size 36 --cuda_device 1 --n_channels 128 --kernel_size 5 : 11071 MB

batch_size = args.batch_size
cuda = torch.cuda.is_available()
torch.cuda.set_device(args.cuda_device)

if args.expname == 'notset':
    expname = datetime.datetime.now().isoformat()[:-10]+'_'+'_'.join(sys.argv).replace('/','-')
else:
    expname = args.expname

save_dir = os.path.join('models', expname)
res_dir = os.path.join(args.result_dir, expname)
os.makedirs(save_dir, exist_ok=True)
os.makedirs(res_dir, exist_ok=True)




def findLastCheckpoint(save_dir):
    file_list = glob.glob(os.path.join(save_dir, 'model_*.pth'))
    if file_list:
        epochs_exist = []
        for file_ in file_list:
            result = re.findall(".*model_(.*).pth.*", file_)
            epochs_exist.append(int(result[0]))
        initial_epoch = max(epochs_exist)
    else:
        initial_epoch = 0
    return initial_epoch


def log(*args, **kwargs):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S:"), *args, **kwargs)

class sum_squared_error(_Loss):  # PyTorch 0.4.1
    """
    Definition: sum_squared_error = 1/2 * nn.MSELoss(reduction = 'sum')
    The backward is defined as: input-target
    """
    def __init__(self, size_average=None, reduce=None, reduction='sum'):
        super(sum_squared_error, self).__init__(size_average, reduce, reduction)

    def forward(self, input, target):
        # return torch.sum(torch.pow(input-target,2), (0,1,2,3)).div_(2)
        return torch.nn.functional.mse_loss(input, target, size_average=None, reduce=None,
                                            reduction='sum').div_(2)



if __name__ == '__main__':
    # model selection
    print('===> Building model')
    if args.model == 'DnCNN':
        model = nnModules.DnCNN(depth=args.depth, n_channels=args.n_channels, find_noise=args.find_noise, kernel_size=args.kernel_size)
    elif args.model == 'RedCNN':
        model = nnModules.RedCNN()
    else:
        exit(args.model+' not implemented.')
    initial_epoch = findLastCheckpoint(save_dir=save_dir)  # load the last model in matconvnet style
    if initial_epoch > 0:
        print('resuming by loading epoch %03d' % initial_epoch)
        # model.load_state_dict(torch.load(os.path.join(save_dir, 'model_%03d.pth' % initial_epoch)))
        model = torch.load(os.path.join(save_dir, 'model_%03d.pth' % initial_epoch))
    model.train()
    # criterion = nn.MSELoss(reduction = 'sum')  # PyTorch 0.4.1
    #criterion = sum_squared_error()
    criterion = pytorch_msssim.MSSSIM()
    if cuda:
        model = model.cuda()
        # device_ids = [0]
        # model = nn.DataParallel(model, device_ids=device_ids).cuda()
        # criterion = criterion.cuda()
    DDataset = DenoisingDataset(args.train_data)
    DLoader = DataLoader(dataset=DDataset, num_workers=8, drop_last=True, batch_size=batch_size, shuffle=True)
    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    scheduler = MultiStepLR(optimizer, milestones=[args.epoch*.02, args.epoch*.06, args.epoch*.14, args.epoch*.30, args.epoch*.62, args.epoch*.78, args.epoch*.86], gamma=0.5)  # learning rates
    for epoch in range(initial_epoch, args.epoch):
        scheduler.step(epoch)  # step to the learning rate in this epcoh
        epoch_loss = 0
        start_time = time.time()

        for n_count, batch_yx in enumerate(DLoader):
            optimizer.zero_grad()
            if cuda:
                batch_x, batch_y = batch_yx[1].cuda(), batch_yx[0].cuda()
            loss = criterion(model(batch_y), batch_x)
            epoch_loss += loss.item()
            loss.backward()
            optimizer.step()
            if n_count % 10 == 0:
                print('%4d %4d / %4d loss = %2.4f' % (epoch+1, n_count, len(DDataset)//batch_size, loss.item()/batch_size))
        elapsed_time = time.time() - start_time

        log('epoch = %4d , loss = %4.4f , time = %4.2f s' % (epoch+1, epoch_loss/n_count, elapsed_time))
        np.savetxt(res_dir+'/train_result_'+str(epoch)+'.txt', np.hstack((epoch+1, epoch_loss/n_count, elapsed_time)), fmt='%2.4f')
        # torch.save(model.state_dict(), os.path.join(save_dir, 'model_%03d.pth' % (epoch+1)))
        torch.save(model, os.path.join(save_dir, 'model_%03d.pth' % (epoch+1)))





