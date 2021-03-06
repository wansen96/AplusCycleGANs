import networks
import torch.nn as nn
import torch
import itertools
import argparse
import os
from torch.autograd import Variable
import numpy as np
from dataloader import torchimshow
from matplotlib import pyplot as plt

class cycleGAN(nn.Module):
    class default_option():
        def __init__(self):
            self.lr = 0.0002
            self.b1 = 0.5
            self.b2 = 0.999
            self.channels = 3
            self.out_channels = 3
            self.n_residual_blocks = 9
            self.save_dir = "./saved_models/"
            
            self.lambda_identity=0.5
            self.lambda_A=10.
            self.lambda_B=10.
            self.image_size = (256,256)
            self.device = 'cuda'

    def __init__(self, opt=None):
        '''

        :param opt:
        :param nic:
        '''
        super(cycleGAN, self).__init__()
        if opt == None:
            opt = self.default_option()
        self.opt = opt
        nic = opt.channels
        noc = opt.out_channels
        # model & loss names
        self.model_names = ['GenA', 'GenB', 'DisA', 'DisB']
        self.loss_names = ['D_A', 'G_A', 'cycle_A', 'idt_A', 'D_B', 'G_B', 'cycle_B', 'idt_B']
        # define Generator
        """
        ResnetGenerator(nic, out_channels, num_residual_blocks, ngf)
        """
        self.GenA = networks.ResnetGenerator(nic, noc, opt.n_residual_blocks)
        self.GenB = networks.ResnetGenerator(nic, noc, opt.n_residual_blocks)

        # define Discriminator
        """
        Discriminator(nic)
        """
        self.DisA = networks.Discriminator(nic, opt.image_size)
        self.DisB = networks.Discriminator(nic, opt.image_size)
        
        # criterion define loss function
        """
        GAN Loss
        Cycle-Consistency Loss
        Identity Loss
        """
        self.criterion_GAN = nn.MSELoss()
        self.criterion_Cycle = nn.L1Loss()
        self.criterion_idt = nn.L1Loss()
        self.optimizers = []

        # define optimizer
        self.optimizer_G = torch.optim.Adam(itertools.chain(self.GenA.parameters(), self.GenB.parameters()),
                                            lr=opt.lr, betas=(opt.b1, 0.999))
        self.optimizer_D = torch.optim.Adam(itertools.chain(self.DisA.parameters(), self.DisB.parameters()),
                                            lr=opt.lr, betas=(opt.b1, 0.999))
        self.optimizers.append(self.optimizer_G)
        self.optimizers.append(self.optimizer_D)

        #
        self.save_dir = opt.save_dir
        self.device = opt.device
        
        step_size = 100
        self.schedulers = [networks.get_scheduler(optimizer, step_size) for optimizer in self.optimizers]

    def set_input(self, real_A, real_B):
        #self_device here
        self.real_A = real_A
        self.real_B = real_B
        if torch.cuda.is_available():
            cuda = True
        else:
            cuda = False
                
        Tensor = torch.cuda.FloatTensor if cuda else torch.Tensor
        self.valid = Variable(Tensor(np.ones((real_A.size(0), *self.DisA.output_shape))), requires_grad=False).to(self.device)
        self.fake = Variable(Tensor(np.zeros((real_A.size(0), *self.DisA.output_shape))), requires_grad=False).to(self.device)

    def forward(self):
        self.fake_B = self.GenA(self.real_A)
        self.rec_A = self.GenB(self.fake_B)
        self.fake_A = self.GenB(self.real_B)
        self.rec_B = self.GenA(self.fake_A)

    # Calculate Loss
    def backward_D_basic(self, netD, real, fake):
        '''
        Calculate loss for discriminator
        '''
        # Real
        pred_real = netD(real)
        loss_D_real = self.criterion_GAN(pred_real, self.valid)
        # Fake
        pred_fake = netD(fake.detach())
        loss_D_fake = self.criterion_GAN(pred_fake, self.fake)
        # Combined loss and calculate gradients
        loss_D = (loss_D_real + loss_D_fake) * 0.5
        loss_D.backward()
        return loss_D

    def backward_D_A(self):
        self.loss_D_A = self.backward_D_basic(self.DisA, self.real_B, self.fake_B)

    def backward_D_B(self):
        self.loss_D_B = self.backward_D_basic(self.DisB, self.real_A, self.fake_A)

    def backward_G(self):
        '''
        Calculate loss for generator
        '''
        lambda_idt = self.opt.lambda_identity
        lambda_A = self.opt.lambda_A
        lambda_B = self.opt.lambda_B
        # Identity loss
        if lambda_idt > 0:
            # G_A should be identity if real_B is fed: ||G_A(B) - B||
            self.idt_A = self.GenA(self.real_B)
            self.loss_idt_A = self.criterion_idt(self.idt_A, self.real_B) * lambda_B * lambda_idt
            # G_B should be identity if real_A is fed: ||G_B(A) - A||
            self.idt_B = self.GenB(self.real_A)
            self.loss_idt_B = self.criterion_idt(self.idt_B, self.real_A) * lambda_A * lambda_idt
        else:
            self.loss_idt_A = 0
            self.loss_idt_B = 0

        # GAN loss D_A(G_A(A))
        self.loss_G_A = self.criterion_GAN(self.DisA(self.fake_B), self.valid)
        # GAN loss D_B(G_B(B))
        self.loss_G_B = self.criterion_GAN(self.DisB(self.fake_A), self.valid)
        # Forward cycle loss || G_B(G_A(A)) - A||
        self.loss_cycle_A = self.criterion_Cycle(self.rec_A, self.real_A) * lambda_A
        # Backward cycle loss || G_A(G_B(B)) - B||
        self.loss_cycle_B = self.criterion_Cycle(self.rec_B, self.real_B) * lambda_B
        # combined loss and calculate gradients
        self.loss_G = self.loss_G_A + self.loss_G_B + self.loss_cycle_A + self.loss_cycle_B + self.loss_idt_A + self.loss_idt_B
        self.loss_G.backward()

    def set_requires_grad(self, nets, requires_grad=False): 
        if not isinstance(nets, list): 
            nets = [nets] 
        for net in nets:      
            if net is not None:
                for param in net.parameters(): 
                    param.requires_grad = requires_grad  
        
    # Optimizer Step
    def optimize_step(self):
        """Calculate losses, gradients, and update network weights; called in every training iteration"""
        # forward
        self.forward()  # compute fake images and reconstruction images.
        # G_A and G_B
        self.set_requires_grad([self.DisA, self.DisB], False)  # Ds require no gradients when optimizing Gs
        self.optimizer_G.zero_grad()  # set G_A and G_B's gradients to zero
        self.backward_G()  # calculate gradients for G_A and G_B
        self.optimizer_G.step()  # update G_A and G_B's weights
        # D_A and D_B
        self.set_requires_grad([self.DisA, self.DisB], True)
        self.optimizer_D.zero_grad()  # set D_A and D_B's gradients to zero
        self.backward_D_A()  # calculate gradients for D_A
        self.backward_D_B()  # calculate graidents for D_B
        self.optimizer_D.step()  # update D_A and D_B's weights


    # save model
    def save_networks(self, epoch):
        for name in self.model_names:
            if isinstance(name, str):
                save_filename = '%s_net_%s.pth' % (epoch, name)
                save_path = os.path.join(self.save_dir, save_filename)
                net = getattr(self, name)
                torch.save(net.cpu().state_dict(), save_path)
                net.cuda(0)
                    

    # load model
    def load_networks(self, epoch):
        for name in self.model_names:
            if isinstance(name, str):
                load_filename = '%s_net_%s.pth' % (epoch, name)
                load_path = os.path.join(self.save_dir, load_filename)
                net = getattr(self, name)
                if isinstance(net, torch.nn.DataParallel):
                    net = net.module
                print('loading the model from %s' % load_path)
                state_dict = torch.load(load_path, map_location=self.device)
                if hasattr(state_dict, '_metadata'):
                    del state_dict._metadata

                # patch InstanceNorm checkpoints prior to 0.4
                for key in list(state_dict.keys()):  # need to copy keys here because we mutate in loop
                    self.__patch_instance_norm_state_dict(state_dict, net, key.split('.'))
                net.load_state_dict(state_dict)

    def __patch_instance_norm_state_dict(self, state_dict, module, keys, i=0):
        key = keys[i]
        if i + 1 == len(keys):  # at the end, pointing to a parameter/buffer
            if module.__class__.__name__.startswith('InstanceNorm') and \
                    (key == 'running_mean' or key == 'running_var'):
                if getattr(module, key) is None:
                    state_dict.pop('.'.join(keys))
            if module.__class__.__name__.startswith('InstanceNorm') and \
                    (key == 'num_batches_tracked'):
                state_dict.pop('.'.join(keys))
        else:
            self.__patch_instance_norm_state_dict(state_dict, getattr(module, key), keys, i + 1)

    def update_learning_rate(self):
        '''Update learning rates for all the networks by learning rate scheduler'''
        for scheduler in self.schedulers:
                scheduler.step()

        lr = self.optimizers[0].param_groups[0]['lr']
        print('learning rate = %.7f' % lr)

    def get_current_losses(self):
        losses = {}
        for name in self.loss_names:
            if isinstance(name, str):
                losses[name] = float(getattr(self, 'loss_' + name))
        return losses
    def test(self):
        with torch.no_grad():
            self.forward()

    def show_latest_img(self):
        '''
        display latest training image by following order:
        |real image A| fake image B generated from A | recovered image A|
        |real image B| fake image A generated from B | recovered image B|
        '''
        fig, axes = plt.subplots(ncols=3, nrows=2, figsize=(7,6))
        torchimshow(self.real_A[0],ax=axes[0][0])
        axes[0][0].set_title('real image  A')
        torchimshow(self.fake_B[0],ax=axes[0][1])
        axes[0][1].set_title('style transfered to B')
        torchimshow(self.rec_A[0],ax=axes[0][2])
        axes[0][2].set_title('recovered image A')
        torchimshow(self.real_B[0],ax=axes[1][0])
        axes[1][0].set_title('real image B')
        torchimshow(self.fake_A[0],ax=axes[1][1])
        axes[1][1].set_title('style transfered to A')
        torchimshow(self.rec_B[0],ax=axes[1][2])
        axes[1][2].set_title('recovered image B')
        return axes
            
    def return_loss(self):
        return [self.loss_D_A,self.loss_G_A,
                self.loss_cycle_A,self.loss_idt_A,
                self.loss_D_B,self.loss_G_B,
                self.loss_cycle_B,self.loss_idt_B]
    
    def save_checkpoint(self, checkpoint, epoch):
        '''
        save checkpoints (losses, current epoch)
        '''
        save_filename = '%s_net_checkpoint.pth' % (epoch)
        save_path = os.path.join(self.save_dir, save_filename)
        torch.save(checkpoint, save_path)
        
    def load_checkpoint(self, epoch):
        '''
        load checkpoints (losses, current epoch)
        if file does not exist, create new checkpoint
        '''
        load_filename = '%s_net_checkpoint.pth' % (epoch)
        load_path = os.path.join(self.save_dir, load_filename)
        if os.path.isfile(load_path):
            checkpoint = torch.load(load_path)
        else:
            print(f'{load_filename} do not exist')
            print('start new training')
            checkpoint = {}
            checkpoint['Loss'] = []
            checkpoint['current epoch'] = 0
        return checkpoint
                


