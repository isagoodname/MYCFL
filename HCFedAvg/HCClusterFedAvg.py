import collections
from typing import Dict
import gc
import HCClusterTree
import torch
import random
import copy
import math
from sklearn.decomposition import PCA
from matplotlib import pyplot as plt
import Args
from Args import ClientInServerData
import KMeansPP
import datetime
import MMDLoss
import Model
import Data
from torchvision import datasets, transforms
from torch.utils.data import DataLoader, TensorDataset
import torch.nn.functional as F
import numpy as np
import torch.optim as optim
import time
import os
import multiprocessing

from DataScientist import pca_deduce


def train(global_model_state_dict, dataset_dict, worker_id, device, args):
    model = Model.init_model(args.model_name)
    model.load_state_dict(global_model_state_dict)
    optimizer = optim.SGD(model.parameters(), lr=args.lr)

    train_loader= init_local_dataloader(dataset_dict, args)
    model.train()

    model.to(device)

    for local_epoch in range(args.local_epochs):
        for batch_index, (batch_data, batch_label) in enumerate(train_loader):
            optimizer.zero_grad()
            batch_data = batch_data.to(device)
            batch_label = batch_label.to(device)

            pred = model(batch_data)

            loss = F.nll_loss(pred, batch_label)

            loss.backward()

            optimizer.step()

    model.to('cpu')

    data_len = sum(dataset_dict['data_len'].values())
    cost = 0
    cost += sum([param.nelement() for param in model.parameters()])

    # loss, acc = test(model, test_loader, device)

    return {'model_dict': model.state_dict(),
            'data_len': data_len,
            'id': worker_id,
            'cost': cost * 4}


def init_local_dataloader(dataset_dict, args):

    train_dataset = TensorDataset(dataset_dict['data'],
                                  dataset_dict['label'].to(torch.long))

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, drop_last=True)
    #
    # test_dataset = TensorDataset(dataset_dict['data_test'],
    #                              dataset_dict['label_test'].to(torch.long))
    #
    # test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=True, drop_last=True)

    return train_loader


def test(model, dataset_loader, device):
    model.eval()
    test_loss = 0
    correct = 0
    model = model.to(device)
    with torch.no_grad():
        for data, target in dataset_loader:
            data = data.to(device)
            target = target.to(device)
            output = model(data)
            test_loss += F.nll_loss(output, target, reduction='sum').item()
            pred = output.argmax(1, keepdim=True)
            correct += pred.eq(target.view_as(pred)).sum().item()

    test_len = len(dataset_loader.dataset)
    test_loss /= test_len
    model.to('cpu')
    return test_loss, correct / test_len


def avg(model_dict, local_model_dicts):
    total_len = 0
    for model_inf in local_model_dicts:
        total_len += model_inf['data_len']
    for key in model_dict.keys():
        model_dict[key] *= 0
        for remote_model in local_model_dicts:
            model_dict[key] += (remote_model['model_dict'][key] * remote_model['data_len'] / total_len)
    return model_dict


def pca_dim_deduction(high_dim_data, max_dim):
    pca = PCA(n_components=max_dim, whiten=True)
    return pca.fit_transform(high_dim_data)


def draw_dynamic(clients_dict, clients_clusters):
    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')
    # ax = fig.add_subplot()
    plt.title("10-Class Data Distribution")

    markers = []
    colors = []

    while True:
        data_ = [[] for i in range(len(clients_dict))]

        if len(data_) > 0:
            keys = []
            values = []
            for key, value in clients_dict.items():
                keys.append(key)
                values.append(value[0].tolist())
            print(" values : ")

            new_value = pca_dim_deduction(np.array(values), 3)

            for i, id in enumerate(keys):
                data_[id] = new_value[i]
            print("clients_clusters : ")
            print(clients_clusters)
            point_style_list = ['lightcoral', 'darkkhaki', 'green', 'lightblue', 'mistyrose']
            for id, workers_id in enumerate(clients_clusters):
                for worker_id in workers_id:
                    ax.scatter(data_[worker_id][0], data_[worker_id][1], data_[worker_id][2], c=point_style_list[id], marker="^")
        plt.draw()
        plt.pause(1)
        ax.clear()


def main(args):
    train_workers_dataset, test_clusters_dataset = Data.load_data(args)

    train_workers = [i for i in range(args.worker_num)]

    global_model_test_data = init_test_dataset_loader(args.dataset_name, args.batch_size)

    # FedAvg算法的模型
    FedAvg_global_model = Model.init_model(args.model_name)

    # 每个客户端的 局部模型，初始化时为相同的模型

    global_model = Model.init_model(args.model_name)

    FedAvg_global_model.load_state_dict(global_model.state_dict())

    clients_model: Dict[int, ClientInServerData] = {}

    pre_clients_model = {}

    ClusterManager = HCClusterTree.HCClusterManager()

    device = torch.device("cuda:0" if torch.cuda.is_available() and args.cuda else "cpu")

    TotalLoss = []
    TotalAcc = []

    FedAvg_Loss = []
    FedAvg_Acc = []

    for epoch in range(args.global_round):
        # print("Epoch :{}\t,".format(epoch + 1), end='')
        cluster_clients_train = random.sample(train_workers, args.worker_train)

        epoch_loss = []
        epoch_acc = []

        FedAvg_local_models = []

        for worker_id in cluster_clients_train:
            # print(epoch, "  worker : ", worker_id)

            if clients_model.get(worker_id) is None:
                clients_model[worker_id] = ClientInServerData(worker_id, global_model.state_dict(), worker_id, epoch)
                train_model_dict = global_model.state_dict()

            else:
                train_model_dict = ClusterManager.get_cluster_by_id(clients_model[worker_id].InClusterID).get_avg_cluster_model_copy()
                clients_model[worker_id].TrainRound = epoch

            pre_clients_model[worker_id] = copy.deepcopy(train_model_dict)
            train_eval= train(train_model_dict
                                          ,train_workers_dataset[worker_id]
                                          ,worker_id
                                          ,device
                                          ,args)

            FedAvg_train_eval= train(copy.deepcopy(FedAvg_global_model.state_dict())
                                          , train_workers_dataset[worker_id]
                                          , worker_id
                                          , device
                                          , args)
            FedAvg_local_models.append(FedAvg_train_eval)


            # print( loss,"   " ,acc)
            # epoch_loss.append(loss)
            # epoch_acc.append(acc)
            clients_model[worker_id].set_client_info(train_eval['model_dict'], train_eval['data_len'])


        # 计算相似性矩阵
        similarity_matrix = update_client_similarity_matrix(clients_model, pre_clients_model)

        ClusterManager.reset_similarity_matrix(similarity_matrix)

        ClusterManager.HCClusterDivide()

        # ClusterManager.print_divide_result()

        # ClusterManager.UpdateClusterAvgModel(clients_model, cluster_clients_train)
        ClusterManager.UpdateClusterAvgModelWithTime(clients_model, cluster_clients_train)
        epoch_loss = []
        epoch_acc = []

        ## 集群准确性测试
        for cluster_id, Cluster in ClusterManager.CurrentClusters.items():
            test_dataset = test_clusters_dataset[cluster_id % 5]
            test_dataloader = init_local_dataloader(test_dataset, args)
            test_model = Model.init_model(args.model_name)
            test_model.load_state_dict(Cluster.AvgClusterModelDict)
            loss, acc = test(test_model, test_dataloader, device)
            epoch_loss.append(loss)
            epoch_acc.append(acc)

        ## FedAvg聚合
        FedAvg_global_model.load_state_dict(avg(FedAvg_global_model.state_dict(), FedAvg_local_models))

        loss, acc = test(FedAvg_global_model, global_model_test_data, device)
        FedAvg_Loss.append(loss)
        FedAvg_Acc.append(acc)
        TotalLoss.append(np.mean(epoch_loss))
        TotalAcc.append(np.mean(epoch_acc))
        print("Epoch: {}\t, FedAvg\t: Acc : {}\t, Loss : {}\t".format(epoch, FedAvg_Acc[epoch], FedAvg_Loss[epoch]))
        print("Epoch: {}\t, HCCFL\t: Acc : {}\t, Loss : {}\t".format(epoch, TotalAcc[epoch], TotalLoss[epoch]))

    SavePath = args.save_path + 'round_100_WithOutTimeAvg_HCCFL_FedAvg_Loss_Acc_0'
    # torch.save(client_update_grad,
    #            SavePath + '.pt')
    torch.save(FedAvg_Loss,
               SavePath + '_FedAvg_Loss.pt')
    torch.save(FedAvg_Acc,
               SavePath + '_FedAvg_Acc.pt')
    # torch.save(client_update_grad_with_,
    #            SavePath + '_weighting_grad.pt')
    torch.save(TotalLoss,
               SavePath + '_HCCFL_Loss.pt')
    torch.save(TotalAcc,
               SavePath + '_HCCFL_Acc.pt')


def L2_Distance(tensor1, tensor2):
    # Value = 0
    # for i in range(tensor1.shape[0]):
    #     Value += math.pow(tensor1[i].item() - tensor2[i].item(), 2)
    # return Value

    UpSum = 0
    for i in range(tensor1.shape[0]):
        UpSum += tensor1[i].item() * tensor2[i].item()
    DownSum1 = 0
    DownSum2 = 0
    for i in range(tensor1.shape[0]):
        DownSum1 += tensor1[i].item() * tensor1[i].item()
    DownSum1 = DownSum1 ** 0.5
    for i in range(tensor2.shape[0]):
        DownSum2 += tensor2[i].item() * tensor2[i].item()
    DownSum2 = DownSum2 ** 0.5

    return abs(1 - UpSum / (DownSum1 * DownSum2))


def avg_deep_param(model_dict, pre_model_dict):
    AvgParam = torch.zeros(model_dict['fc4.weight'].shape[0])
    for i in range(model_dict['fc4.weight'].shape[1]):
        for j in range(model_dict['fc4.weight'].shape[0]):
            AvgParam[j] = AvgParam[j] + model_dict['fc4.weight'][j][i]
    return AvgParam / model_dict['fc4.weight'].shape[1]


def update_client_similarity_matrix(clients_model: Dict[int, ClientInServerData], pre_clients_model):
    similarity_matrix = {client_id_l:{client_id_r: 0.0 for client_id_r in clients_model.keys()} for client_id_l in clients_model.keys()}
    for client_id_l, Client_l in clients_model.items():
        client_l_avg_param = avg_deep_param(Client_l.ModelStaticDict, pre_clients_model[client_id_l])
        for client_id_r, Client_r in clients_model.items():
            client_r_avg_param = avg_deep_param(Client_r.ModelStaticDict, pre_clients_model[client_id_r])
            similarity_matrix[client_id_l][client_id_r] = L2_Distance(client_l_avg_param, client_r_avg_param)
    return similarity_matrix
    #
    # for client_id_l, clients in similarity_matrix.items():
    #     client_l_deep_avg_param = avg_deep_param(clients_model[client_id_l].ModelStaticDict)
    #     for client_id_r, SimValue in clients.items():
    #         client_r_deep_avg_param = avg_deep_param(clients_model[client_id_r].ModelStaticDict)
    #         similarity_matrix[client_id_l][client_id_r] = L2_Distance(client_l_deep_avg_param, client_r_deep_avg_param)


def init_test_dataset_loader(dataset_name, batch_size):
    if dataset_name == 'mnist':
        dataset = datasets.MNIST(root='../data', train=False, transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,))]))
        return DataLoader(dataset, batch_size=batch_size, shuffle=False)
    elif dataset_name == 'cifar10':
        dataset = datasets.CIFAR10(root='../data', train=False, transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))]))
        return DataLoader(dataset, batch_size=batch_size, shuffle=False)
    else:
        pass


def save(global_test_eval, global_cost, model_dict, args):
    args.to_string('FedPro')
    dir_path = args.save_path + '/' + 'Experiment'
    dir_path_id = 0
    while True:
        save_path = dir_path + str(dir_path_id)
        if not os.path.exists(save_path):
            os.mkdir(save_path)
            break
        else:
            dir_path_id += 1

    torch.save(global_cost,
               save_path + '/' + 'Global_Cost.pt')
    torch.save(global_test_eval,
               save_path + '/' + 'Global.pt')
    torch.save(model_dict,
               save_path + '/' + 'Model_Dict.pt')

    f = open(save_path + '/实验描述', 'w', encoding='UTF-8')
    f.write(args.Arg_string)
    f.write('\n' + str(datetime.datetime.now()))
    f.close()


if __name__ == '__main__':
    MyArgs = Args.Arguments()
    main(MyArgs)
