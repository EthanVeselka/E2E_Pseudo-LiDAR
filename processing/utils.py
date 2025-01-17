import numpy as np
import sys
import os
import csv
import shutil

sys.path.append("..")
BASE_DIR = ".."

# def read_chunk(reader, chunk_size):
#     data = {}
#     for _ in range(chunk_size):
#         ret = reader.read_next()
#         for k, v in ret.items():
#             if k not in data:
#                 data[k] = []
#             data[k].append(v)
#     data["header"] = data["header"][0]
#     return data


def clean_output(datapath):

    config = "config.ini"
    datapath = os.path.join(os.getcwd(), BASE_DIR, datapath)
    os.chdir(datapath)

    for episode in os.listdir(datapath):
        if episode == ".gitignore":
            continue
        if episode == "calibmatrices.txt":
            continue
        if episode == "config.ini":
            continue

        os.chdir(episode)
        curr_dir = os.path.join(datapath, episode)

        for iteration in os.listdir(curr_dir):
            if iteration == config:
                continue
            os.chdir(iteration)
            curr_dir = os.path.join(curr_dir, iteration)

            for timestamp in os.listdir(curr_dir):
                if timestamp == config:
                    continue

                os.chdir(timestamp)
                curr_dir = os.path.join(curr_dir, timestamp)

                for frame in os.listdir(curr_dir):
                    if frame == "config.ini":
                        continue
                    os.chdir(frame)
                    if not os.path.exists("output"):
                        os.chdir("..")
                        continue
                    else:
                        shutil.rmtree(os.path.join(curr_dir, frame, "output"))
                        os.chdir("..")
                os.chdir("..")
            os.chdir("..")
            curr_dir = os.path.join(curr_dir, "../../")
        os.chdir("..")

    os.chdir("../output")
    if os.path.exists("train.csv"):
        os.remove("train.csv")
    if os.path.exists("val.csv"):
        os.remove("val.csv")
    if os.path.exists("test.csv"):
        os.remove("test.csv")
