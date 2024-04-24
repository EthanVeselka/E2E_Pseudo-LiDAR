from __future__ import print_function

import argparse
import os
import time
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.parallel
import torch.optim as optim
import torch.utils.data
import torch.nn.functional as F

BASE_DIR = "../../.."
sys.path.append(BASE_DIR)

from models.disparity_model.stackhourglass import PSMNet as stackhourglass
from logger import setup_logger
import processing.pseudo_lidar.custom_loader as ls
import processing.pseudo_lidar.custom_dataset as DA


parser = argparse.ArgumentParser(description="PSMNet")
parser.add_argument("--maxdisp", type=int, default=192, help="maximum disparity")
parser.add_argument("--epochs", type=int, default=1, help="number of epochs to train")
parser.add_argument("--loadmodel", default=None, help="load pretrained model")
parser.add_argument("--savemodel", default="../saved_models", help="model save name")
parser.add_argument("--btrain", type=int, default=1, help="size of batch")
parser.add_argument("--start_epoch", type=int, default=1)
parser.add_argument(
    "--cuda", action="store_true", default=False, help="Enables CUDA training"
)
parser.add_argument(
    "--seed", type=int, default=1, metavar="S", help="random seed (default: 1)"
)
parser.add_argument(
    "--lr_scale", type=int, default=200, metavar="S", help="lr scale (default: 200)"
)
parser.add_argument(
    "--datapath",
    default="carla_data/data/",
    help="datapath",
)
parser.add_argument(
    "--split_file",
    default="carla_data/output",
    help="training data sampling indices",
)

args = parser.parse_args()
args.cuda = args.cuda and torch.cuda.is_available()
torch.manual_seed(args.seed)
if args.cuda:
    torch.cuda.manual_seed(args.seed)

if not os.path.isdir(args.savemodel):
    os.makedirs(args.savemodel)
print(os.path.join(args.savemodel, "training.log"))
log = setup_logger(os.path.join(args.savemodel, "training.log"))


datapath = os.path.join(BASE_DIR, args.datapath)
split_file = os.path.join(BASE_DIR, args.split_file)
(
    all_left_img,
    all_right_img,
    all_left_disp,
) = ls.dataloader(datapath, split_file, "train")

TrainImgLoader = torch.utils.data.DataLoader(
    DA.myImageFloder(all_left_img, all_right_img, all_left_disp, True),
    batch_size=args.btrain,
    shuffle=True,
    num_workers=1,
    drop_last=False,
)

(
    all_left_img,
    all_right_img,
    all_left_disp,
) = ls.dataloader(datapath, split_file, "val")

ValImgLoader = torch.utils.data.DataLoader(
    DA.myImageFloder(all_left_img, all_right_img, all_left_disp, True),
    batch_size=args.btrain,
    shuffle=True,
    num_workers=1,
    drop_last=False,
)


model = stackhourglass(args.maxdisp)
if args.cuda:
    model = nn.DataParallel(model)
    model.cuda()
if args.loadmodel != None:
    log.info("load model " + args.loadmodel)
    state_dict = torch.load(args.loadmodel)
    model.load_state_dict(state_dict["state_dict"])

print(
    "Number of model parameters: {}".format(
        sum([p.data.nelement() for p in model.parameters()])
    )
)

optimizer = optim.Adam(model.parameters(), lr=0.1, betas=(0.9, 0.999))


def train(imgL, imgR, disp_L):
    model.train()
    imgL = torch.FloatTensor(imgL)
    imgR = torch.FloatTensor(imgR)
    disp_L = torch.FloatTensor(disp_L)

    if args.cuda:
        imgL, imgR, disp_true = imgL.cuda(), imgR.cuda(), disp_L.cuda()
    else:
        disp_true = disp_L
    # ---------
    mask = disp_true > 0
    mask.detach_()
    # ----

    optimizer.zero_grad()
    output1, output2, output3 = model(imgL, imgR)

    output1 = torch.squeeze(output1, 1)
    output2 = torch.squeeze(output2, 1)
    output3 = torch.squeeze(output3, 1)
    loss = (
        0.5 * F.smooth_l1_loss(output1[mask], disp_true[mask], size_average=True)
        + 0.7 * F.smooth_l1_loss(output2[mask], disp_true[mask], size_average=True)
        + F.smooth_l1_loss(output3[mask], disp_true[mask], size_average=True)
    )
    loss.backward()
    optimizer.step()

    return loss.item()


def test(imgL, imgR, disp_true):
    model.eval()
    imgL = torch.FloatTensor(imgL)
    imgR = torch.FloatTensor(imgR)

    if args.cuda:
        imgL, imgR = imgL.cuda(), imgR.cuda()

    with torch.no_grad():
        output3 = model(imgL, imgR)

    pred_disp = output3.data.cpu()

    # computing 3-px error#
    true_disp = disp_true
    index = np.argwhere(true_disp > 0)
    disp_true[index[0][:], index[1][:], index[2][:]] = np.abs(
        true_disp[index[0][:], index[1][:], index[2][:]]
        - pred_disp[index[0][:], index[1][:], index[2][:]]
    )
    correct = (disp_true[index[0][:], index[1][:], index[2][:]] < 3) | (
        disp_true[index[0][:], index[1][:], index[2][:]]
        < true_disp[index[0][:], index[1][:], index[2][:]] * 0.05
    )
    torch.cuda.empty_cache()

    return 1 - float(torch.sum(correct)) / float(len(index[0]))


def adjust_learning_rate(optimizer, epoch):
    if epoch <= args.lr_scale:
        lr = 0.001
    else:
        lr = 0.0001
    for param_group in optimizer.param_groups:
        param_group["lr"] = lr


def main():
    max_acc = 0
    max_epo = 0
    start_full_time = time.time()

    for epoch in range(args.start_epoch, args.epochs + 1):
        total_train_loss = 0
        adjust_learning_rate(optimizer, epoch)

        ## training ##
        for batch_idx, (imgL_crop, imgR_crop, disp_crop_L) in enumerate(TrainImgLoader):

            start_time = time.time()

            loss = train(imgL_crop, imgR_crop, disp_crop_L)
            print(
                "Iter %d training loss = %.3f , time = %.2f"
                % (batch_idx, loss, time.time() - start_time)
            )
            total_train_loss += loss
            acc = test(imgL_crop, imgR_crop, disp_crop_L)
            print("Batch Error: ", acc)
        
        print("\n")
        print("-----------------------------------")
        print(
            "Epoch %d: average training loss = %.3f"
            % (epoch, total_train_loss / len(TrainImgLoader))
        )
        total = 0
        counter = 0
        for batch_idx, (imgL_crop, imgR_crop, disp_crop_L) in enumerate(ValImgLoader):
            total += test(imgL_crop, imgR_crop, disp_crop_L)
            counter += 1
        print(f"Epoch {epoch}: average validation error = {total/counter}")
        print("-----------------------------------")
        print("\n")

        # SAVE
        if not os.path.isdir(args.savemodel):
            os.makedirs(args.savemodel)
        savefilename = args.savemodel + "/finetune_" + str(epoch) + ".tar"
        torch.save(
            {
                "epoch": epoch,
                "state_dict": model.state_dict(),
                "train_loss": total_train_loss / len(TrainImgLoader),
            },
            savefilename,
        )


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


if __name__ == "__main__":
    main()
