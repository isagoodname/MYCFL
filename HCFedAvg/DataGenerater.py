# 读取并 预处理数据集， 同时根据需求生成 客户端 数据索引列表
from typing import *

import torch
import torchvision
from torch.utils.data import DataLoader, TensorDataset
from torchvision import datasets, transforms
import random
import numpy as np
import Args
from torchvision.utils import save_image


class ClientDataInfo:
    def __init__(self, in_cluster_id, client_id, is_rot = False, rot = 0):
        self.InClusterID = in_cluster_id
        self.ClientID = client_id
        self.DataIndex = []
        self.DatasetLoader = None

        # 是否旋转， 旋转 rot * 90度
        self.IsRot = is_rot
        self.Rot = rot

class DatasetGen:
    def __init__(self, args: Args.Arguments):
        # 预处理后的数据 列表
        self.Data = None
        # 预处理后的数据 标签列表
        self.Label = None

        self.LabelDataIndex = None
        self.TestLabelDataIndex = None

        # 预处理后的 测试集 数据 和 标签
        self.TestData = None
        self.TestLabel = None

        self.Trans = None

        # 客户端的 本地数据标签
        self.ClientsDataInfo: List[ClientDataInfo] = []
        self.mArgs: Args.Arguments = args

        self.ClusterTestDataIndex = [[] for i in range(self.mArgs.cluster_number)]

        self.init_client_data_list()
        self.load_dataset()
        self.normalize_dataset()
        self.divide_clients_data_index()
        # self.get_client_DataLoader(27)
        # self.print_img()

    def print_img(self):
        for i in range(4):
            rot_imgs = torch.rot90(self.Data[0][0], k=int(i))
            save_image(rot_imgs, "%d.png" % i, nrow=5, normalize=True)


    # 将全部集群的测试集合并为 FedAvg的测试集数据
    def get_fedavg_test_DataLoader(self):
        data_index = []
        for index in self.ClusterTestDataIndex:
            data_index.extend(index)
        data = self.TestData[data_index]
        label = self.TestLabel[data_index]
        test_dataset = TensorDataset(self.TestData,
                                     self.TestLabel)

        test_loader = DataLoader(test_dataset, batch_size=64, shuffle=False, drop_last=True)
        return test_loader

    def get_client_DataLoader(self, client_id):
        # 如果是 'labels' 直接生成Loader
        data_index = self.ClientsDataInfo[client_id].DataIndex
        data = self.Data[data_index]
        label = self.Label[data_index]
        # print(label.shape)
        train_dataset = TensorDataset(data,
                                      label)

        train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True, drop_last=True)
        # save_image(data[1], "%d.png" % 1, nrow=5, normalize=True)

        return train_loader
        # 如果是 'rot' 旋转数据生成Loader

    def get_cluster_test_DataLoader(self, cluster_id):
        cluster_labels = self.mArgs.data_info['data_labels'][cluster_id]
        data_index = []
        for label in cluster_labels:
            data_index.extend(self.TestLabelDataIndex[label])

        test_data = self.TestData[data_index]
        test_label = self.TestLabel[data_index]
        test_dataset = TensorDataset(test_data,
                                     test_label)

        test_loader = DataLoader(test_dataset, batch_size=64, shuffle=False, drop_last=True)
        return test_loader

        # data_index = self.ClusterTestDataIndex[cluster_id]
        #
        #
        # data = self.TestData[data_index]
        # label = self.TestLabel[data_index]
        # test_dataset = TensorDataset(data,
        #                               label)
        #
        # test_loader = DataLoader(test_dataset, batch_size=64, shuffle=False, drop_last=True)
        # return test_loader

    def init_client_data_list(self):
        cluster_num = self.mArgs.cluster_number
        assert cluster_num == len(self.mArgs.data_info['data_labels'])
        assert cluster_num == len(self.mArgs.data_info['data_rot'])

        for i in range(self.mArgs.worker_num):
            if self.mArgs.data_info['divide_type'] == 'rot':
                self.ClientsDataInfo.append(ClientDataInfo((i % cluster_num), i, True, self.mArgs.data_info['data_rot'][i % cluster_num]))
            elif self.mArgs.data_info['divide_type'] == 'labels':
                self.ClientsDataInfo.append(ClientDataInfo((i % cluster_num), i))
    # 加载预处理数据集
    def load_dataset(self):
        if self.mArgs.dataset_name == 'mnist':
            dataset = datasets.MNIST('data', train=True, download=True)
            dataset_test = datasets.MNIST('data', train=False, download=True)
            # 数据集转换标准化
            trans = transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize((0.1307,), (0.3081,))
            ])
        elif self.mArgs.dataset_name == 'cifar10':
            dataset = datasets.CIFAR10('data', train=True, download=True)
            dataset_test = datasets.CIFAR10('data', train=False, download=True)
            trans = transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
            ])
        else:
            dataset = None
            dataset_test = None
            trans = None

        return dataset, dataset_test, trans


    def normalize_dataset(self):
        dataset, dataset_test, trans = self.load_dataset()
        assert dataset is not True
        # 将图片的int类型改为float
        dataset.data = dataset.data.float()
        dataset_test.data = dataset_test.data.float()

        # 图片数据标准化
        data_list = []
        for i, data in enumerate(dataset.data):
            data_list.append(trans(data.numpy()).numpy())

        data_test_list = []
        for i, data in enumerate(dataset_test.data):
            data_test_list.append(trans(data.numpy()).numpy())

        self.LabelDataIndex = [[] for _ in range(self.mArgs.dataset_labels_number)]
        for i, label in enumerate(dataset.targets):
            self.LabelDataIndex[label.item()].append(i)

        self.TestLabelDataIndex = [[] for _ in range(self.mArgs.dataset_labels_number)]
        for i, label in enumerate(dataset_test.targets):
            self.TestLabelDataIndex[label.item()].append(i)


        self.Data = torch.tensor(np.array(data_list))
        self.Label = dataset.targets
        self.TestData = torch.tensor(np.array(data_test_list))
        self.TestLabel = dataset_test.targets

    def divide_clients_data_index(self):
        if self.mArgs.data_info['divide_type'] == 'labels':
            cluster_labels = self.mArgs.data_info['data_labels']
            MaxLen = 0
            for labels_list in cluster_labels:
                if len(labels_list) > MaxLen:
                    MaxLen = len(labels_list)

            # 计算每个集群的 每种标签的数据量占比
            labels_ratio = [[] for i in range(self.mArgs.dataset_labels_number)]
            for labels_list in cluster_labels:
                for label in labels_list:
                    labels_ratio[label].append(1.0 / len(labels_list))

            MaxRatio = 0
            MaxLabel = 0
            MinDataSize = 99999
            for label, label_ratio in enumerate(labels_ratio):
                if sum(label_ratio) > MaxRatio or (
                        sum(label_ratio) == MaxRatio and MinDataSize > len(self.TestLabelDataIndex[label])):
                    MinDataSize = len(self.TestLabelDataIndex[label])
                    MaxRatio = sum(label_ratio)
                    MaxLabel = label

            ClusterDataSize = int(len(self.TestLabelDataIndex[MaxLabel]) / MaxRatio)

            labels_current_pos = [0 for i in range(self.mArgs.dataset_labels_number)]
            test_cluster_labels_list = {i: {j: [] for j in cluster_labels[i]} for i in range(self.mArgs.cluster_number)}
            for cluster_id, cluster_label in test_cluster_labels_list.items():
                for label_id, label_index in cluster_label.items():
                    labels_len = len(cluster_labels[cluster_id])
                    start_pos = labels_current_pos[label_id]
                    end_pos = int(ClusterDataSize / labels_len) + start_pos
                    test_cluster_labels_list[cluster_id][label_id] = self.TestLabelDataIndex[label_id][start_pos: end_pos]
                    self.ClusterTestDataIndex[cluster_id].extend(self.TestLabelDataIndex[label_id][start_pos: end_pos])
                    labels_current_pos[label_id] = end_pos

            for cluster_id, cluster_label in test_cluster_labels_list.items():
                for label_id, label_index in cluster_label.items():
                    random.shuffle(label_index)

            MaxRatio = 0
            MaxLabel = 0
            MinDataSize = 99999
            for label, label_ratio in enumerate(labels_ratio):
                if sum(label_ratio) > MaxRatio or (sum(label_ratio) == MaxRatio and MinDataSize > len(self.LabelDataIndex[label])):
                    MinDataSize = len(self.LabelDataIndex[label])
                    MaxRatio = sum(label_ratio)
                    MaxLabel = label

            ClusterDataSize = int(len(self.LabelDataIndex[MaxLabel]) / MaxRatio)

            labels_current_pos = [0 for i in range(self.mArgs.dataset_labels_number)]
            cluster_labels_list = {i: {j: [] for j in cluster_labels[i]} for i in range(self.mArgs.cluster_number)}
            for cluster_id, cluster_label in cluster_labels_list.items():
                for label_id, label_index in cluster_label.items():
                    labels_len = len(cluster_labels[cluster_id])
                    start_pos = labels_current_pos[label_id]
                    end_pos = int(ClusterDataSize / labels_len) + start_pos
                    cluster_labels_list[cluster_id][label_id] = self.LabelDataIndex[label_id][start_pos: end_pos]
                    labels_current_pos[label_id] = end_pos

            cluster_clients_list = {i: [] for i in range(self.mArgs.cluster_number)} # 每个集群 客户端数量
            for client_data in self.ClientsDataInfo:
                cluster_clients_list[client_data.InClusterID].append(client_data.ClientID)

            for cluster_id in range(self.mArgs.cluster_number):
                cluster_client_num = len(cluster_clients_list[cluster_id])
                for label, labels_list in cluster_labels_list[cluster_id].items():
                    label_size = len(labels_list)//cluster_client_num
                    start_pos = 0
                    end_pos = label_size
                    for client_id in cluster_clients_list[cluster_id]:
                        self.ClientsDataInfo[client_id].DataIndex.extend(labels_list[start_pos: end_pos])
                        start_pos = end_pos
                        end_pos += label_size

        elif self.mArgs.data_info['divide_type'] == 'rot':
            cluster_number = self.mArgs.cluster_number
            assert cluster_number == len(self.mArgs.data_info['data_rot'])
            client_number_pre_cluster = self.mArgs.worker_num // cluster_number
            for cluster_id in range(cluster_number):
                
                pass

        else:
            pass

if __name__ == '__main__':
    args = Args.Arguments()
    DatasetGen(args)







