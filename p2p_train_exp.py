from __future__ import print_function
import argparse
import os
from math import log10
from dataset_torch_3 import DenoisingDataset
import time
import datetime
import sys

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
import torch.backends.cudnn as cudnn
from random import random
import datetime
from lib import pytorch_ssim

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
parser.add_argument('--lr', type=float, default=0.0003, help='initial learning rate for adam')
parser.add_argument('--lr_policy', type=str, default='plateau', help='learning rate policy: lambda|step|plateau|cosine')
parser.add_argument('--lr_decay_iters', type=int, default=50, help='multiply by a gamma every lr_decay_iters iterations')
parser.add_argument('--beta1', type=float, default=0.75, help='beta1 for adam. default=0.5')
parser.add_argument('--threads', type=int, default=4, help='number of threads for data loader to use')
parser.add_argument('--seed', type=int, default=123, help='random seed to use. Default=123')

parser.add_argument('--weight_ssim', type=float, default=0.4, help='weight on SSIM term in objective')
parser.add_argument('--weight_L1', type=float, default=0.1, help='weight on L1 term in objective')
parser.add_argument('--train_data', nargs='*', help='(space-separated) Path(s) to the pre-cropped training data (default: '+'datasets/train/NIND_160_128'+')')
parser.add_argument('--time_limit', default=172800, type=int, help='Time limit in seconds')
parser.add_argument('--find_noise', action='store_true', help='(DnCNN) Model noise if set, otherwise generate clean image')
parser.add_argument('--compressionmin', type=str, default=100, help='Minimum compression level ([1,100], default=100)')
parser.add_argument('--compressionmax', type=int, default=100, help='Maximum compression level ([1,100], default=100)')
parser.add_argument('--sigmamin', type=int, default=0, help='Minimum sigma (noise) value ([0,100], default=0)')
parser.add_argument('--sigmamax', type=int, default=0, help='Maximum sigma (noise) value ([0,100], default=0)')
parser.add_argument('--yval', type=str, help='Use a specified noise value for y. Default is to use all that is available, possible values are "x" (use the ground-truth, useful with artificial noise or compression) or any ISO value s.a. ISO64000')
parser.add_argument('--test_reserve', nargs='*', help='Space separated list of image sets to be reserved for testing')
parser.add_argument('--exact_reserve', action='store_true', help='If this is set, the test reserve string must match exactly, otherwise any set that contains a test reserve string will be ignored')
parser.add_argument('--do_sizecheck', action='store_true', help='Skip crop size check for faster initial loading (rely on filename only)')
parser.add_argument('--cuda_device', default=0, type=int, help='Device number (default: 0, typically 0-3)')
parser.add_argument('--expname', type=str, help='Experiment name used to save and/or load results and models (default autogenerated from time+CLI)')
parser.add_argument('--resume', action='store_true', help='Look for an experiment with the same parameters and continue (to force continuing an experiment with different parameters use --expname instead)')
parser.add_argument('--result_dir', default='results/train', type=str, help='Directory where results are stored (default: results/train)')
parser.add_argument('--models_dir', default='models', type=str, help='Directory where models are saved/loaded (default: models)')
parser.add_argument('--lr_gamma', default=.75, type=float, help='Learning rate decrease rate for plateau, StepLR (default: 0.75)')
parser.add_argument('--lr_step_size', default=5, type=int, help='Step size for StepLR, patience for plateau scheduler')
parser.add_argument('--model', default='UNet', type=str, help='Model type (UNet, Resnet, HunkyNet)')
parser.add_argument('--D_ratio', default=0.33, type=float, help='How often D learns compared to G ( (0,1], default 1)')
parser.add_argument('--lr_min', default=0.00000005, type=float, help='Minimum learning rate (training stops when both lr are below threshold, default: 0.00000005)')
parser.add_argument('--min_ssim_l', default=0.15, type=float, help='Minimum SSIM score before using GAN loss')
parser.add_argument('--post_fail_ssim_num', default=25, type=int, help='How many times SSIM is used exclusively when min_ssim_l threshold is not met')
parser.add_argument('--lr_update_min_D_ratio', default=0.2, type=float, help='Minimum use of the discriminator (vs SSIM) for LR reduction')
parser.add_argument('--keep_D', action='store_true', help='Keep using the discriminator once its threshold has been reached')
parser.add_argument('--not_conditional', action='store_true', help='Discriminator does not see noisy image')
parser.add_argument('--netD', default='basic', type=str, help='Discriminator network type (basic, HunkyDisc, HunkyDisc)')
# TODO simpler discriminator architecture
args = parser.parse_args()

print(args)


def find_experiment():
    exp = None
    bname = ('_'.join(sys.argv).replace('/','-')).replace('_--resume','')
    for adir in os.listdir(args.models_dir):
        if adir[17:]==bname:
            exp = adir
    return exp


if args.expname:
    expname = args.expname
else:
    if args.resume:
        expname = find_experiment()
        if expname == None:
            sys.exit('Error: cannot resume experiment (404)')
    else:
        expname = datetime.datetime.now().isoformat()[:-10]+'_'+'_'.join(sys.argv).replace('/','-')


cudnn.benchmark = True

torch.manual_seed(args.seed)
torch.cuda.manual_seed(args.seed)

if args.train_data == None or args.train_data == []:
    train_data = ['datasets/train/NIND_160_128']
else:
    train_data = args.train_data


save_dir = os.path.join('models', expname)
res_dir = os.path.join(args.result_dir, expname)



print('===> Loading datasets')
DDataset = DenoisingDataset(train_data, compressionmin=args.compressionmin, compressionmax=args.compressionmax, sigmamin=args.sigmamin, sigmamax=args.sigmamax, test_reserve=args.test_reserve, yval=args.yval, do_sizecheck=args.do_sizecheck, exact_reserve=args.exact_reserve)
training_data_loader = DataLoader(dataset=DDataset, num_workers=args.threads, drop_last=True, batch_size=args.batch_size, shuffle=True)
#testing_data_loader = DataLoader(dataset=test_set, num_workers=args.threads, batch_size=args.test_batch_size, shuffle=False)

torch.cuda.set_device(args.cuda_device)
device = torch.device("cuda:"+str(args.cuda_device))

D_n_layers = args.input_nc if args.not_conditional else args.input_nc + args.output_nc

print('===> Building models')
net_g = define_G(args.input_nc, args.output_nc, args.ngf, 'batch', False, 'normal', 0.02, gpu_id=device, net_type=args.model)
net_d = define_D(D_n_layers, args.ndf, args.netD, gpu_id=device)

criterionGAN = GANLoss().to(device)
criterionL1 = nn.L1Loss().to(device)
#criterionMSE = nn.MSELoss().to(device)
criterionSSIM = pytorch_ssim.SSIM()

# setup optimizer
optimizer_g = optim.Adam(net_g.parameters(), lr=args.lr, betas=(args.beta1, 0.999))
optimizer_d = optim.Adam(net_d.parameters(), lr=args.lr, betas=(args.beta1, 0.999))
net_g_scheduler = get_scheduler(optimizer_g, args, generator=True)
net_d_scheduler = get_scheduler(optimizer_d, args, generator=False)

if args.netD != 'HunkyNet':
    loss_crop_lb = int((DDataset.cs-DDataset.ucs)/2)
    loss_crop_up = loss_crop_lb+DDataset.ucs
else:   #tmp. make disc more flexible.
    loss_crop_lb = 0
    loss_crop_up = int(DDataset.cs)

keep_D = False

start_time = time.time()
iterations_before_d = 0
for epoch in range(args.epoch_count, args.niter + args.niter_decay + 1):
    # train
    total_loss_d = 0
    total_loss_g_D = 0
    total_loss_g_std = 0
    num_train_d = 0
    num_train_g_D = 0
    num_train_g_std = 0
    for iteration, batch in enumerate(training_data_loader, 1):
        discriminator_learns = random() < args.D_ratio
        # forward
        cleanimg, noisyimg = batch[0].to(device), batch[1].to(device)
        gnoisyimg = net_g(noisyimg)
        if discriminator_learns or iteration == 1:


            ######################
            # (1) Update D network
            ######################

            optimizer_d.zero_grad()

            if args.not_conditional:
                fake_ab = gnoisyimg
                #print(fake_ab.shape)
                pred_fake = net_d.forward(fake_ab.detach())
                # train with fake
            else:
                fake_ab = torch.cat((noisyimg, gnoisyimg), 1)
                #print(fake_ab.shape)
                pred_fake = net_d.forward(fake_ab.detach())
            loss_d_fake = criterionGAN(pred_fake, False)
            if args.not_conditional:
                real_ab = cleanimg
                pred_real = net_d.forward(real_ab)
            else:
            # train with real
                real_ab = torch.cat((noisyimg, cleanimg), 1)
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
            loss_d_item = loss_d.item()
            total_loss_d += loss_d_item
            num_train_d += 1
        else:
            loss_d_item=float('nan')

        # First, G(A) should fake the discriminator

        loss_g_ssim = (1-criterionSSIM(gnoisyimg, cleanimg))
        loss_g_L1 = criterionL1(gnoisyimg, cleanimg)
        loss_g_item_str = 'L(SSIM: {:.4f}, L1: {:.4f}'.format(loss_g_ssim, loss_g_L1)
        # use D
        if keep_D or (loss_g_ssim.item() < args.min_ssim_l and iterations_before_d < 1):
            if args.keep_D:
                keep_D = True
            loss_g_ssim *=  args.weight_ssim
            loss_g_L1 *= args.weight_L1
            if args.not_conditional:
                fake_ab = gnoisyimg
                pred_fake = net_d.forward(fake_ab)
            else:
                fake_ab = torch.cat((noisyimg, gnoisyimg), 1)
                pred_fake = net_d.forward(fake_ab)
            loss_g_gan = criterionGAN(pred_fake, True)
            loss_g_item_str += ', D(G(y),y): {:.4f})'.format(loss_g_gan)
            loss_g_gan *= (1-args.weight_ssim-args.weight_L1)
            #print(loss_g_gan.item())
            #loss_g = criterionGAN(pred_fake, True)
            # Second, G(A) = B
            #loss_g_l1 = criterionL1(gnoisyimg[:,:,loss_crop_lb:loss_crop_up, loss_crop_lb:loss_crop_up], cleanimg[:,:,loss_crop_lb:loss_crop_up, loss_crop_lb:loss_crop_up]) * args.lamb
            loss_g = loss_g_gan + loss_g_ssim + loss_g_L1
            loss_g.backward()
            loss_g_item = loss_g.item()
            loss_g_item_str += ') = '+'{:.4f}'.format(loss_g)
            total_loss_g_D += loss_g_item
            num_train_g_D += 1
        else:
            if loss_g_ssim.item() > args.min_ssim_l:
                iterations_before_d = args.post_fail_ssim_num
            else:
                iterations_before_d -= 1
            loss_g_ssim = loss_g_ssim / (args.weight_ssim+args.weight_L1) * args.weight_ssim
            loss_g_L1 = loss_g_L1 / (args.weight_ssim+args.weight_L1) * args.weight_L1
            loss_g = loss_g_ssim + loss_g_L1
            loss_g.backward()
            loss_g_item = loss_g.item()
            total_loss_g_std += loss_g_item
            loss_g_item_str += ') = {:.4f}'.format(loss_g_item)
            num_train_g_std += 1

        optimizer_g.step()

        print("===> Epoch[{}]({}/{}): Loss_D: {:.4f} Loss_G: {}".format(
            epoch, iteration, len(training_data_loader), loss_d_item, loss_g_item_str))
    if num_train_g_D > 5:
        update_learning_rate(net_d_scheduler, optimizer_d, loss_avg=total_loss_d/num_train_d)
    if num_train_g_D > num_train_g_std*args.lr_update_min_D_ratio:
        print('Generator average D_loss: '+str(total_loss_g_D/num_train_g_D))
        update_learning_rate(net_g_scheduler['D'], optimizer_g, loss_avg=total_loss_g_D/num_train_g_D)
    else:
        update_learning_rate(net_g_scheduler['SSIM'], optimizer_g, loss_avg=total_loss_g_std/num_train_g_std)
    if num_train_g_std > 0:
        print('Generator average std loss: '+str(total_loss_g_std/num_train_g_std))
    print('Discriminator average loss: '+str(total_loss_d/num_train_d))

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
    try:
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
    except OSError as err:
        save_dir = save_dir[0:255]
        os.makedirs(save_dir)
    #if not os.path.exists(res_dir):
    #    os.makedirs(res_dir)
    net_g_model_out_path = os.path.join(save_dir, "netG_model_epoch_%d.pth" % epoch)
    net_d_model_out_path = os.path.join(save_dir, "netD_model_epoch_%d.pth" % epoch)
    torch.save(net_g, net_g_model_out_path)
    torch.save(net_d, net_d_model_out_path)
    print("Checkpoint saved to {} at {}".format(save_dir, datetime.datetime.now().isoformat()))
    if args.time_limit is not None and args.time_limit < time.time() - start_time:
        print('Time is up.')
        break
    elif optimizer_g.param_groups[0]['lr'] < args.lr_min and optimizer_d.param_groups[0]['lr'] < args.lr_min:
        print('Minimum learning rate reached.')
        break
