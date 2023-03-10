import h5py
import numpy as np
import matplotlib.pyplot as plt
import os
import torch
from data_preprocessing import Feature_Selector_Dataset
import discharge_model
import full_model


"""
於'Data-driven prediction of battery cycle life before capacity degradation'中使用的dataset
由124顆商用LFP電池(APR18650M1A)組成 以快充及4C放電循環至EoL
其額定電容量為1.11Ah 額定電壓為3.3V
資料被分為三個bath
"""
def mat_to_npy(save_path='Severson_Dataset/npdata_each_cell/', cycle_length=100):
    """
    將mat檔中需要的資料截取至pkl檔
    """
    filename = ['Severson_Dataset/2017-05-12_batchdata_updated_struct_errorcorrect.mat',
                'Severson_Dataset/2017-06-30_batchdata_updated_struct_errorcorrect.mat',
                'Severson_Dataset/2018-04-12_batchdata_updated_struct_errorcorrect.mat']

    # 各batch中discharge部分有問題的電池 要加以清理
    b1_err = [0, 1, 2, 3, 4, 8, 10, 12, 13, 18, 22, 14, 15]
    b2_err = [1, 6, 9, 10, 21, 25, 12, 15, 44]
    b3_err = [23, 32, 37]
    err_list = [b1_err, b2_err, b3_err]
    batch_name = ['b1c', 'b2c', 'b3c']
    for b in range(len(filename)): # batch數
        f = h5py.File(filename[b], 'r')
        batch = f['batch']
        num_cells = batch['summary'].shape[0]
        for i in range(num_cells): # 該batch下的電池cell數量
            if i in err_list[b]:
                print('skip err cell: batch %d, cell_id %d'%(b+1, i))
                continue
            Cycle_life = f[batch['cycle_life'][i, 0]][()]
            Qc_summary = np.hstack(f[batch['summary'][i, 0]]['QCharge'][0, :].tolist())
            Qd_summary = np.hstack(f[batch['summary'][i, 0]]['QDischarge'][0, :].tolist())
            Chargetime = np.hstack(f[batch['summary'][i, 0]]['chargetime'][0, :].tolist())
            Tavg = np.hstack(f[batch['summary'][i, 0]]['Tavg'][0, :].tolist())
            Tmin = np.hstack(f[batch['summary'][i, 0]]['Tmin'][0, :].tolist())
            Tmax = np.hstack(f[batch['summary'][i, 0]]['Tmax'][0, :].tolist())
            key = batch_name[b] + str(i).zfill(2)
            # 儲存循環間資訊
            summary = np.vstack([Qc_summary, Qd_summary, Tmin, Tmax, Tavg, Chargetime]) # shape:(6, n_cycle)
            np.save(save_path+key+'_summary', summary)
            
            cycles = f[batch['cycles'][i, 0]]
            cycle_info = []
            for j in range(1, cycle_length+1): # 選擇前n個cyle
                temper = np.hstack((f[cycles['T'][j, 0]]))
                current = np.hstack((f[cycles['I'][j, 0]]))
                voltage = np.hstack((f[cycles['V'][j, 0]]))
                Qc = np.hstack((f[cycles['Qc'][j, 0]]))
                Qd = np.hstack((f[cycles['Qd'][j, 0]]))
                Qdd = np.diff(np.diff(Qd)) # 放電容量二次微分
                ch_s = 0 # 充電開始
                ch_e = np.where(current==0)[0][1] # 充電結束, 電流歸零
                dis_s = np.where(np.diff(Qd)>=1e-3)[0][0] # 放電開始
                dis_e = np.where(Qdd>1e-4)[0][-1]+1 # 放電結束

                charge_info = linear_interpolation([Qc[ch_s:ch_e], voltage[ch_s:ch_e], current[ch_s:ch_e], temper[ch_s:ch_e]])
                discharge_info = linear_interpolation([Qd[dis_s:dis_e], voltage[dis_s:dis_e], current[dis_s:dis_e], temper[dis_s:dis_e]])
                cycle_info.append(np.expand_dims(np.hstack([charge_info, discharge_info]), axis=0))
            np.save(save_path+key+'_cycle', np.concatenate(cycle_info, axis=0))
            print(key+' finished')


def linear_interpolation(seq, points=500):
    interp_list = []
    for s in seq:
        interp_id = np.linspace(0, len(s)-1, points)
        interp_list.append(np.interp(interp_id, np.arange(len(s)), s).reshape(1, -1))       
    return np.vstack(interp_list)


def data_visulaization():
    path = 'Severson_Dataset/npdata_each_cell/'
    cmap = plt.get_cmap('coolwarm')
    filename = sorted(os.listdir(path))
    print('n cells: %d' % (len(filename)//2))
    eols = []
    for i in range((len(filename))//2):
        summary = np.load(path+filename[2*i+1])
        eol = summary.shape[1]
        eols.append(eol)
        plt.plot(np.arange(eol-1), summary[1, 1:], c=cmap(1-(eol-326)/(1934-326)), alpha=0.7)
    print(min(eols), max(eols))
    # plt.colorbar()
    plt.show()
    plt.close()

def train_val_split(train_ratio=0.8, seed=15, folder='Severson_Dataset/feature_selector_discharge/'):
    """
    分成三組: EoL<600, 600<=EoL<=1200, EoL>1200
    training, validation和testing要平均分配樣本
    high:mid:low = 46:67:8
    """
    path = 'Severson_Dataset/npdata_each_cell/'
    filename = sorted(os.listdir(path))
    features, targets, all_summary = [], [], []
    for i in range((len(filename))//2):
        curve = np.load(path+filename[2*i])
        summary = np.load(path+filename[2*i+1])
        eol, chargetime_end = len(summary[0]), summary[5, -1]
        features.append(curve)
        targets.append(np.array([eol, chargetime_end]))
        all_summary.append(np.expand_dims(summary[:, :100], axis=0))

    # 根據seed設定隨機調整順序
    dataset = list(zip(features, targets, all_summary))
    np.random.seed(seed)
    np.random.shuffle(dataset) 
    features = [f[0] for f in dataset]
    targets = [f[1] for f in dataset]
    all_summary = [f[2] for f in dataset]
    split_point = int(len(targets)*train_ratio)
    print(np.concatenate(all_summary[:split_point]).shape)
    np.save(folder+'trn_features', np.concatenate(features[:split_point]))
    np.save(folder+'val_features', np.concatenate(features[split_point:]))
    np.save(folder+'trn_targets', np.repeat(np.vstack(targets[:split_point]), 100, axis=0))
    np.save(folder+'val_targets', np.repeat(np.vstack(targets[split_point:]), 100, axis=0))
    np.save(folder+'trn_summary', np.vstack(all_summary[:split_point]))
    np.save(folder+'val_summary', np.vstack(all_summary[split_point:]))


def predictor1_preprocess(folder='Severson_Dataset/feature_selector_discharge/'):
    selectors = []
    for i in range(1, 3, 1):
        model = discharge_model.__dict__['Dim_Reduction_'+str(i)](4, 1, 0.0).cuda()
        model.load_state_dict(torch.load('models/discharge/Dim_Reduction_'+str(i)+'_seed41.pth'))
        model.eval()
        selectors.append(model)
    trn_set = Feature_Selector_Dataset(train=True, pred_target='EOL', part='discharge')
    val_set = Feature_Selector_Dataset(train=False, pred_target='EOL', part='discharge')
    trn_summary = np.load(folder+'trn_summary.npy')
    val_summary = np.load(folder+'val_summary.npy')
    trn_feature = np.zeros((len(trn_summary), 8, 100))
    val_feature = np.zeros((len(val_summary), 8, 100))
    trn_feature[:, :6, :] = trn_summary[:, :, :100]
    val_feature[:, :6, :] = val_summary[:, :, :100]
    with torch.no_grad():
        for i in range(len(trn_summary)):
            for j, slt in enumerate(selectors):
                feature = slt(torch.tensor(trn_set[(100*i):(100*(i+1))][0]).cuda().float())
                trn_feature[i, 6+j] = feature.detach().cpu().numpy().squeeze()
        for i in range(len(val_summary)):
            for j, slt in enumerate(selectors):
                feature = slt(torch.tensor(val_set[(100*i):(100*(i+1))][0]).cuda().float())
                val_feature[i, 6+j] = feature.detach().cpu().numpy().squeeze()
    np.save(folder+'predictor1_trn_feature', trn_feature)
    np.save(folder+'predictor1_val_feature', val_feature)


def predictor3_preprocess(folder='Severson_Dataset/feature_selector_discharge/'):
    selectors = []
    for i in range(1, 5, 1):
        model = full_model.__dict__['Dim_Reduction_'+str(i)](4, 1, 0.0).cuda()
        model.load_state_dict(torch.load('models/full/Dim_Reduction_'+str(i)+'_seed41.pth'))
        model.eval()
        selectors.append(model)
    dis_trn_set = Feature_Selector_Dataset(train=True, pred_target='EOL', part='discharge')
    dis_val_set = Feature_Selector_Dataset(train=False, pred_target='EOL', part='discharge')
    ch_trn_set = Feature_Selector_Dataset(train=True, pred_target='EOL', part='charge')
    ch_val_set = Feature_Selector_Dataset(train=False, pred_target='EOL', part='charge')
    trn_summary = np.load(folder+'trn_summary.npy')
    val_summary = np.load(folder+'val_summary.npy')
    trn_feature = np.zeros((len(trn_summary), 10, 100))
    val_feature = np.zeros((len(val_summary), 10, 100))
    trn_feature[:, :6, :] = trn_summary[:, :, :100]
    val_feature[:, :6, :] = val_summary[:, :, :100]
    with torch.no_grad():
        for i in range(len(trn_summary)):
            for j, slt in enumerate(selectors):
                trn_set = dis_trn_set if j<2 else ch_trn_set
                feature = slt(torch.tensor(trn_set[(100*i):(100*(i+1))][0]).cuda().float())
                trn_feature[i, 6+j] = feature.detach().cpu().numpy().squeeze()
        for i in range(len(val_summary)):
            for j, slt in enumerate(selectors):
                val_set = dis_val_set if j<2 else ch_val_set
                feature = slt(torch.tensor(val_set[(100*i):(100*(i+1))][0]).cuda().float())
                val_feature[i, 6+j] = feature.detach().cpu().numpy().squeeze()
    np.save(folder+'predictor3_trn_feature', trn_feature)
    np.save(folder+'predictor3_val_feature', val_feature)


# mat_to_npy()
# train_val_split()
# predictor1_preprocess()
# predictor3_preprocess()
# data_visulaization()