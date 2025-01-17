from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
import torch.nn.functional as F
import numpy as np
import time
import math
import sys
from PIL import Image


BASE_DIR = "../../.."
sys.path.append(BASE_DIR)

from models.PSMNet.stackhourglass import PSMNet as stackhourglass
from processing.pseudo_lidar import transforms as preprocess
import processing.pseudo_lidar.custom_loader as ls
import processing.pseudo_lidar.custom_dataset as DA

parser = argparse.ArgumentParser(description="PSMNet")
parser.add_argument("--loadmodel", default=None, help="loading model")
parser.add_argument("--maxdisp", type=int, default=192, help="maxium disparity")
parser.add_argument(
    "--all", action="store_true", help="predict all frames or just test"
)
parser.add_argument(
    "--cuda", action="store_true", default=False, help="Enables CUDA training"
)
parser.add_argument(
    "--seed", type=int, default=1, metavar="S", help="random seed (default: 1)"
)
parser.add_argument(
    "--datapath",
    default="carla_data/data",
    help="datapath",
)
parser.add_argument(
    "--split_file",
    default="carla_data/output",
    help="training data sampling indices",
)
parser.add_argument(
    "--save_figure",
    action="store_true",
    help="if true save the png file",
)
parser.add_argument(
    "--test_accuracy",
    action="store_true",
    default=False,
    help="if true output the accuracy of the model",
)
args = parser.parse_args()


args.cuda = args.cuda and torch.cuda.is_available()
torch.manual_seed(args.seed)
if args.cuda:
    torch.cuda.manual_seed(args.seed)


datapath = os.path.join(BASE_DIR, args.datapath)
split_file = os.path.join(BASE_DIR, args.split_file)
task = "all" if args.all else "test"
save_path = "../predictions" if (task == "test") else "output"
test_left_img, test_right_img, true_disps = ls.dataloader(datapath, split_file, task)

TestImgLoader = torch.utils.data.DataLoader(
    DA.myImageFloder(test_left_img, test_right_img, true_disps, True),
    batch_size=1,
    shuffle=True,
    num_workers=1,
    drop_last=False,
)

model = stackhourglass(args.maxdisp)

if args.cuda:
    model = nn.DataParallel(model, device_ids=[0])
    model.cuda()

if args.loadmodel is not None:
    state_dict = torch.load(args.loadmodel)
    model.load_state_dict(state_dict["state_dict"])

print(
    "Number of model parameters: {}".format(
        sum([p.data.nelement() for p in model.parameters()])
    )
)


def test_accuracy(imgL, imgR, disp_true):
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

    if len(index[0]) == 0:
        return 1.0
    return 1 - float(torch.sum(correct)) / float(len(index[0]))


def test(imgL, imgR):
    model.eval()

    if args.cuda:
        imgL = torch.FloatTensor(imgL).cuda()
        imgR = torch.FloatTensor(imgR).cuda()
    else:
        imgL = torch.FloatTensor(imgL)
        imgR = torch.FloatTensor(imgR)

    with torch.no_grad():
        output = model(imgL, imgR)
    output = torch.squeeze(output)
    pred_disp = output.data.cpu().numpy()

    return pred_disp


def main():
    processed = preprocess.get_transform(augment=False)
    total = 0
    counter = 0

    if args.test_accuracy:
        total = 0
        count = 0

        for batch_idx, (imgL, imgR, dispL) in enumerate(TestImgLoader):
            curr = test_accuracy(imgL, imgR, dispL)
            print("frame", count, "error:", str(curr))
            total += curr
            count += 1

        print("Error:", str(total / count), "over", str(count), "frames")
        return

    else:
        if task == "test" and not os.path.exists(save_path):
            os.mkdir(save_path)
        count = 0
        t = 0
        for idx in range(len(test_left_img)):
            print(
                f"Progress: {((idx + 1) / len(test_left_img)) * 100:.2f}% complete",
                end="\r",
            )

            count += 1
            imgL_o = Image.open(test_left_img[idx]).convert("RGB")
            imgR_o = Image.open(test_right_img[idx]).convert("RGB")

            # crop to KITTI size
            w = 1280
            h = 720
            imgL = np.array(imgL_o.crop((0, 0, w, h))).astype("float32")
            imgR = np.array(imgR_o.crop((0, 0, w, h))).astype("float32")
            # imgL = np.array(imgL_o).astype("float32")
            # imgR = np.array(imgR_o).astype("float32")
            imgL = processed(imgL).numpy()
            imgR = processed(imgR).numpy()
            imgL = np.reshape(imgL, [1, 3, imgL.shape[1], imgL.shape[2]])
            imgR = np.reshape(imgR, [1, 3, imgR.shape[1], imgR.shape[2]])
            # pad to 1248x384 (KITTI size)
            # top_pad = 384 - imgL.shape[2]
            # left_pad = 1248 - imgL.shape[3]
            # imgL = np.lib.pad(
            #     imgL,
            #     ((0, 0), (0, 0), (top_pad, 0), (0, left_pad)),
            #     mode="constant",
            #     constant_values=0,
            # )
            # imgR = np.lib.pad(
            #     imgR,
            #     ((0, 0), (0, 0), (top_pad, 0), (0, left_pad)),
            #     mode="constant",
            #     constant_values=0,
            # )

            start_time = time.time()
            pred_disp = test(imgL, imgR)
            t += time.time() - start_time

            # top_pad = 384 - 352
            # left_pad = 1248 - 1200
            # img = pred_disp[top_pad:, :-left_pad]
            img = pred_disp
            frame = test_left_img[idx].split("/")[-2]
            if args.save_figure:
                if task == "test":
                    Image.fromarray(img).convert("RGB").save(
                        save_path + f"/predicted_disp_{frame}.png",
                    )
                else:
                    Image.fromarray(img).convert("RGB").save(
                        "/".join(test_left_img[idx].split("/")[:-1])
                        + "/"
                        + save_path
                        + "/predicted_disp.png",
                    )
            else:
                if task == "test":
                    np.save(save_path + f"/predicted_disp_{frame}.npy", img)
                else:
                    path = (
                        "/".join(test_left_img[idx].split("/")[:-1]) + "/" + save_path
                    )
                    if not os.path.exists(path):
                        os.mkdir(path)
                    np.save(
                        "/".join(test_left_img[idx].split("/")[:-1])
                        + "/"
                        + save_path
                        + "/predicted_disp.npy",
                        img,
                    )
                    print("Saved to path: ", path)
        print("Frames", count, ": average time = %.2f" % (t / count))


if __name__ == "__main__":
    main()
