from __future__ import print_function
import argparse
import os
from math import log10
from dataset_torch_3 import DenoisingDataset
import time

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
import torch.backends.cudnn as cudnn

from p2p_networks import define_G, define_D, GANLoss, get_scheduler, update_learning_rate

# Training settings
parser = argparse.ArgumentParser(description='pix2pix-pytorch-implementation-in-mthesis-denoise')
parser.add_argument('--batch_size', type=int, default=60, help='training batch size')
parser.add_argument('--test_batch_size', type=int, default=1, help='testing batch size')
parser.add_argument('--input_nc', type=int, default=3, help='input image channels')
parser.add_argument('--output_nc', type=int, default=3, help='output image channels')
parser.add_argument('--ngf', type=int, default=64, help='generator filters in first conv layer')
parser.add_argument('--ndf', type=int, default=64, help='discriminator filters in first conv layer')
parser.add_argument('--epoch_count', type=int, default=1, help='the starting epoch count')
parser.add_argument('--niter', type=int, default=100, help='# of iter at starting learning rate')
parser.add_argument('--niter_decay', type=int, default=100, help='# of iter to linearly decay learning rate to zero')
parser.add_argument('--lr', type=float, default=0.0002, help='initial learning rate for adam')
parser.add_argument('--lr_policy', type=str, default='lambda', help='learning rate policy: lambda|step|plateau|cosine')
parser.add_argument('--lr_decay_iters', type=int, default=50, help='multiply by a gamma every lr_decay_iters iterations')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
parser.add_argument('--threads', type=int, default=4, help='number of threads for data loader to use')
parser.add_argument('--seed', type=int, default=123, help='random seed to use. Default=123')
parser.add_argument('--lamb', type=int, default=10, help='weight on L1 term in objective')

parser.add_argument('--train_data', nargs='*', help='(space-separated) Path(s) to the pre-cropped training data (default: '+'datasets/train/NIND_128_96'+')')
parser.add_argument('--time_limit', default=172800, type=int, help='Time limit in seconds')
parser.add_argument('--find_noise', action='store_true', help='(DnCNN) Model noise if set, otherwise generate clean image')
parser.add_argument('--compressionmin', type=str, default=100, help='Minimum compression level ([1,100], default=100)')
parser.add_argument('--compressionmax', type=int, default=100, help='Maximum compression level ([1,100], default=100)')
parser.add_argument('--sigmamin', type=int, default=0, help='Minimum sigma (noise) value ([0,100], default=0)')
parser.add_argument('--sigmamax', type=int, default=0, help='Maximum sigma (noise) value ([0,100], default=0)')
parser.add_argument('--yval', type=str, help='Use a specified noise value for y. Default is to use all that is available, possible values are "x" (use the ground-truth, useful with artificial noise or compression) or any ISO value s.a. ISO64000')
parser.add_argument('--test_reserve', nargs='*', help='Space separated list of image sets to be reserved for testing')
parser.add_argument('--do_sizecheck', action='store_true', help='Skip crop size check for faster initial loading (rely on filename only)')
parser.add_argument('--cuda_device', default=0, type=int, help='Device number (default: 0, typically 0-3)')

args = parser.parse_args()

print(args)

cudnn.benchmark = True

torch.manual_seed(args.seed)
torch.cuda.manual_seed(args.seed)

if args.train_data == None or args.train_data == []:
    train_data = ['datasets/train/NIND_128_96']
else:
    train_data = args.train_data



print('===> Loading datasets')
root_path = "dataset/"
#train_set = get_training_set(root_path + args.dataset, args.direction)
#test_set = get_test_set(root_path + args.dataset, args.direction)
DDataset = DenoisingDataset(train_data, compressionmin=args.compressionmin, compressionmax=args.compressionmax, sigmamin=args.sigmamin, sigmamax=args.sigmamax, test_reserve=args.test_reserve, yval=args.yval, do_sizecheck=args.do_sizecheck)
training_data_loader = DataLoader(dataset=DDataset, num_workers=8, drop_last=True, batch_size=args.batch_size, shuffle=True)
#testing_data_loader = DataLoader(dataset=test_set, num_workers=args.threads, batch_size=args.test_batch_size, shuffle=False)

torch.cuda.set_device(args.cuda_device)
device = torch.device("cuda:"+str(args.cuda_device))

print('===> Building models')
net_g = define_G(args.input_nc, args.output_nc, args.ngf, 'batch', False, 'normal', 0.02, gpu_id=device)
net_d = define_D(args.input_nc + args.output_nc, args.ndf, 'basic', gpu_id=device)

criterionGAN = GANLoss().to(device)
criterionL1 = nn.L1Loss().to(device)
criterionMSE = nn.MSELoss().to(device)

# setup optimizer
optimizer_g = optim.Adam(net_g.parameters(), lr=args.lr, betas=(args.beta1, 0.999))
optimizer_d = optim.Adam(net_d.parameters(), lr=args.lr, betas=(args.beta1, 0.999))
net_g_scheduler = get_scheduler(optimizer_g, args)
net_d_scheduler = get_scheduler(optimizer_d, args)

start_time = time.time()
for epoch in range(args.epoch_count, args.niter + args.niter_decay + 1):
    # train
    for iteration, batch in enumerate(training_data_loader, 1):
        # forward
        real_a, real_b = batch[0].to(device), batch[1].to(device)
        fake_b = net_g(real_a)

        ######################
        # (1) Update D network
        ######################

        optimizer_d.zero_grad()

        # train with fake
        fake_ab = torch.cat((real_a, fake_b), 1)
        pred_fake = net_d.forward(fake_ab.detach())
        loss_d_fake = criterionGAN(pred_fake, False)

        # train with real
        real_ab = torch.cat((real_a, real_b), 1)
        pred_real = net_d.forward(real_ab)
        loss_d_real = criterionGAN(pred_real, True)

        # Combined D loss
        loss_d = (loss_d_fake + loss_d_real) * 0.5

        loss_d.backward()

        optimizer_d.step()

        ######################
        # (2) Update G network
        ######################

        optimizer_g.zero_grad()

        # First, G(A) should fake the discriminator
        fake_ab = torch.cat((real_a, fake_b), 1)
        pred_fake = net_d.forward(fake_ab)
        loss_g_gan = criterionGAN(pred_fake, True)

        # Second, G(A) = B
        loss_g_l1 = criterionL1(fake_b, real_b) * args.lamb

        loss_g = loss_g_gan + loss_g_l1

        loss_g.backward()

        optimizer_g.step()

        print("===> Epoch[{}]({}/{}): Loss_D: {:.4f} Loss_G: {:.4f}".format(
            epoch, iteration, len(training_data_loader), loss_d.item(), loss_g.item()))

    update_learning_rate(net_g_scheduler, optimizer_g)
    update_learning_rate(net_d_scheduler, optimizer_d)

    # test
    #avg_psnr = 0
    #for batch in testing_data_loader:
    #    input, target = batch[0].to(device), batch[1].to(device)
    #
    #    prediction = net_g(input)
    #    mse = criterionMSE(prediction, target)
    #    psnr = 10 * log10(1 / mse.item())
    #    avg_psnr += psnr
    #print("===> Avg. PSNR: {:.4f} dB".format(avg_psnr / len(testing_data_loader)))

    #checkpoint
    if not os.path.exists("checkpoint"):
        os.mkdir("checkpoint")
    if not os.path.exists(os.path.join("checkpoint", args.dataset)):
        os.mkdir(os.path.join("checkpoint", args.dataset))
    net_g_model_out_path = "checkpoint/{}/netG_model_epoch_{}.pth".format(args.dataset, epoch)
    net_d_model_out_path = "checkpoint/{}/netD_model_epoch_{}.pth".format(args.dataset, epoch)
    torch.save(net_g, net_g_model_out_path)
    torch.save(net_d, net_d_model_out_path)
    print("Checkpoint saved to {}".format("checkpoint" + args.dataset))
    if args.time_limit is not None and args.time_limit < time.time() - start_time:
        print('Time is up.')
        break
