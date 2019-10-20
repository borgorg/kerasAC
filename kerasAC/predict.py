from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import time
#graceful shutdown
import psutil
import signal 
import os

#multithreading
#from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import Pool,Process, Queue 

import warnings
import numpy as np
import pysam
import pandas as pd

import tensorflow as tf 
from kerasAC.activations import softMaxAxis1
from .calibrate import * 
from .generators.basic_generator import *
from .generators.tiledb_predict_generator import * 
from kerasAC.config import args_object_from_args_dict
from kerasAC.performance_metrics import *
from kerasAC.custom_losses import *
from kerasAC.metrics import recall, specificity, fpr, fnr, precision, f1
import argparse
import yaml 
import h5py 
import pickle
import numpy as np 
import keras 
from keras.losses import *
from keras.models import Model 
from kerasAC.custom_losses import *
from abstention.calibration import PlattScaling, IsotonicRegression 
import random
import pdb 

def parse_args():
    parser=argparse.ArgumentParser(description='Provide model files  & a dataset, get model predictions')
    
    input_data_path=parser.add_argument_group("input_data_path")
    input_data_path=parser.add_argument_group('input_data_path')
    input_data_path.add_argument("--index_data_path",default=None,help="seqdataloader output hdf5, or tsv file containing binned labels")
    input_data_path.add_argument("--index_tasks",nargs="*",default=None)    
    input_data_path.add_argument("--input_data_path",nargs="+",default=None,help="seq or path to seqdataloader hdf5")
    input_data_path.add_argument("--output_data_path",nargs="+",default=None,help="path to seqdataloader hdf5")
    
    input_data_path.add_argument('--variant_bed',default=None)
    input_data_path.add_argument('--ref_fasta')

    tiledbgroup=parser.add_argument_group("tiledb")
    tiledbgroup.add_argument("--tdb_outputs",nargs="+")
    tiledbgroup.add_argument("--tdb_output_source_attribute",nargs="+",default="fc_bigwig",help="tiledb attribute for use in label generation i.e. fc_bigwig")
    tiledbgroup.add_argument("--tdb_output_flank",nargs="+",type=int,help="flank around bin center to use in generating outputs")
    tiledbgroup.add_argument("--tdb_output_aggregation",nargs="+",default=None,help="method for output aggreagtion; one of None, 'avg','max'")
    tiledbgroup.add_argument("--tdb_output_transformation",nargs="+",default=None,help="method for output transformation; one of None, 'log','log10','asinh'")
    
    tiledbgroup.add_argument("--tdb_inputs",nargs="+")
    tiledbgroup.add_argument("--tdb_input_source_attribute",nargs="+",help="attribute to use for generating model input, or 'seq' for one-hot-encoded sequence")
    tiledbgroup.add_argument("--tdb_input_flank",nargs="+",type=int,help="length of sequence around bin center to use for input")
    tiledbgroup.add_argument("--tdb_input_aggregation",nargs="+",default=None,help="method for input aggregation; one of 'None','avg','max'")
    tiledbgroup.add_argument("--tdb_input_transformation",nargs="+",default=None,help="method for input transformation; one of None, 'log','log10','asinh'")

    tiledbgroup.add_argument("--tdb_indexer",default=None,help="tiledb paths for each input task")
    tiledbgroup.add_argument("--tdb_partition_attribute_for_upsample",default="idr_peak",help="tiledb attribute to use for upsampling, i.e. idr_peak")
    tiledbgroup.add_argument("--tdb_partition_thresh_for_upsample",type=float,default=1,help="values >= partition_thresh_for_upsample within the partition_attribute_for_upsample will be upsampled during training")
    tiledbgroup.add_argument("--upsample_ratio_list_predict",type=float,nargs="*")
    
    tiledbgroup.add_argument("--chrom_sizes",default=None,help="chromsizes file for use with tiledb generator")
    tiledbgroup.add_argument("--tiledb_stride",type=int,default=1)

    input_filtering_params=parser.add_argument_group("input_filtering_params")    
    input_filtering_params.add_argument('--predict_chroms',nargs="*",default=None)
    input_filtering_params.add_argument('--center_on_summit',default=False,action='store_true',help="if this is set to true, the peak will be centered at the summit (must be last entry in bed file or hammock) and expanded args.flank to the left and right")
    input_filtering_params.add_argument("--tasks",nargs="*",default=None)
    
    output_params=parser.add_argument_group("output_params")
    output_params.add_argument('--predictions_hdf5',help='name of hdf5 to save predictions',default=None)
    output_params.add_argument('--performance_metrics_classification_file',help='file name to save accuracy metrics; accuracy metrics not computed if file not provided',default=None)
    output_params.add_argument('--performance_metrics_regression_file',help='file name to save accuracy metrics; accuracy metrics not computed if file not provided',default=None)
    output_params.add_argument('--performance_metrics_profile_file',help='file name to save accuracy metrics; accuracy metrics not computed if file not provided',default=None)
    
    calibration_params=parser.add_argument_group("calibration_params")
    calibration_params.add_argument("--calibrate_classification",action="store_true",default=False)
    calibration_params.add_argument("--calibrate_regression",action="store_true",default=False)        
    
    weight_params=parser.add_argument_group("weight_params")
    weight_params.add_argument('--w1',nargs="*",type=float)
    weight_params.add_argument('--w0',nargs="*",type=float)
    weight_params.add_argument("--w1_w0_file",default=None)


    model_params=parser.add_argument_group("model_params")
    model_params.add_argument('--model_hdf5',help='hdf5 file that stores the model')
    model_params.add_argument('--weights',help='weights file for the model')
    model_params.add_argument('--yaml',help='yaml file for the model')
    model_params.add_argument('--json',help='json file for the model')
    model_params.add_argument('--functional',default=False,help='use this flag if your model is a functional model',action="store_true")
    model_params.add_argument('--squeeze_input_for_gru',action='store_true')
    model_params.add_argument("--expand_dims",default=True)
    model_params.add_argument("--num_inputs",type=int)
    model_params.add_argument("--num_outputs",type=int)
    
    
    parallelization_params=parser.add_argument_group("parallelization")
    parallelization_params.add_argument("--threads",type=int,default=1)
    parallelization_params.add_argument("--max_queue_size",type=int,default=100)

    snp_params=parser.add_argument_group("snp_params")
    snp_params.add_argument('--background_freqs',default=None)
    snp_params.add_argument('--flank',default=500,type=int)
    snp_params.add_argument('--mask',default=10,type=int)
    snp_params.add_argument('--ref_col',type=int,default=None)
    snp_params.add_argument('--alt_col',type=int,default=None)

    parser.add_argument('--batch_size',type=int,help='batch size to use to make model predictions',default=50)
    return parser.parse_args()

def write_predictions(args):
    '''
    separate predictions file for each output/task combination 
    '''
    try:
        out_predictions_suffix=args.predictions_hdf5+".predictions" 
        first=True
        while True:
            pred_df=pred_queue.get()
            if type(pred_df) == str: 
                if pred_df=="FINISHED":
                    return
            if first is True:
                mode='w'
                first=False
                append=False
            else:
                mode='a'
                append=True
            for cur_output_index in range(len(pred_df)):
                cur_pred_df=pred_df[cur_output_index]
                #get cur_pred_df for current output
                if len(cur_pred_df.shape)>2:
                    #tasks in last channel, and there is more than 1 task 
                    num_tasks=cur_pred_df.shape[-1]
                    for cur_task_index in range(num_tasks):
                        #get df for current output/task combination
                        cur_pred_df_task=cur_pred_df[:,:,cur_task_index]
                        #get output file for current output/task combination
                        cur_out_f=str(cur_output_index)+'.task'+str(cur_task_index)+'.'+out_predictions_suffix
                        cur_pred_df_task.to_hdf(cur_out_f,key="data",mode=mode,append=append,format="table",min_itemsize={'index':30})
                else:
                    #one task only, store as task0
                    cur_out_f=str(cur_output_index)+'.task0.'+out_predictions_suffix
                    cur_pred_df.to_hdf(cur_out_f,key="data",mode=mode,append=append,format="table",min_itemsize={'index':30})

    except KeyboardInterrupt:
        #shutdown the pool
        # Kill remaining child processes
        kill_child_processes(os.getpid())
        raise 
    except Exception as e:
        print(e)
        #shutdown the pool
        # Kill remaining child processes
        kill_child_processes(os.getpid())
        raise e

def write_labels(args):
    '''
    separate label file for each output/task combination
    '''
    try:
        out_labels_suffix=args.predictions_hdf5+".labels" 
        first=True
        while True:
            label_df=label_queue.get()
            if type(label_df)==str:
                if label_df=="FINISHED":
                    return
            if first is True:
                mode='w'
                first=False
                append=False
            else:
                mode='a'
                append=True
            for cur_output_index in range(len(label_df)):
                cur_label_df=label_df[cur_output_index]
                #get cur_pred_df for current output
                if len(cur_label_df.shape)>2:
                    #tasks in last channel, and there is more than 1 task 
                    num_tasks=cur_label_df.shape[-1]
                    for cur_task_index in range(num_tasks):
                        #get df for current output/task combination
                        cur_label_df_task=cur_label_df[:,:,cur_task_index]
                        #get output file for current output/task combination
                        cur_out_f=str(cur_output_index)+'.task'+str(cur_task_index)+'.'+out_labels_suffix
                        cur_label_df_task.to_hdf(cur_out_f,key="data",mode=mode,append=append,format="table",min_itemsize={'index':30})
                else:
                    #one task only, store as task0
                    cur_out_f=str(cur_output_index)+'.task0.'+out_labels_suffix
                    cur_label_df.to_hdf(cur_out_f,key="data",mode=mode,append=append,format="table",min_itemsize={'index':30})
    except KeyboardInterrupt:
        #shutdown the pool
        # Kill remaining child processes
        kill_child_processes(os.getpid())
        raise 
    except Exception as e:
        print(e)
        #shutdown the pool
        # Kill remaining child processes
        kill_child_processes(os.getpid())
        raise e

def init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

def kill_child_processes(parent_pid, sig=signal.SIGTERM):
    try:
        parent = psutil.Process(parent_pid)
    except psutil.NoSuchProcess:
        return
    children = parent.children(recursive=True)
    for process in children:
        process.send_signal(sig)
        
def get_weights(args):
    w1=None
    w0=None
    if args.w1_w0_file!=None:
        w1_w0=np.loadtxt(args.w1_w0_file)
        w1=w1_w0[:,0]
        w0=w1_w0[:,1]
    if args.w1!=None:
        w1=args.w1
    if args.w0!=None:
        w0=args.w0 
    return w1,w0


def get_batch_wrapper(idx):
    X,y,coords=test_generator[idx]
    if type(y) is not list:
        y=[y]
    try:
        y=[i.squeeze(axis=-1) for i in y]
    except:
        pass
    if type(X) is not list:
        X=[X]
    
    #represent coords w/ string, MultiIndex not supported in table append mode
    coords=pd.MultiIndex.from_tuples(coords)
    y=[pd.DataFrame(i,index=coords) for i in y]
    return [X,y,coords,idx]


def get_tiledb_predict_generator(args):
    global test_generator
    if args.upsample_ratio_list_predict is not None:
        upsample_ratio_predict=args.upsample_ratio_list_predict[0]
        print("warning! only a single ratio for upsampling supported for tiledb as of now")
    else:
        upsample_ratio_predict=None
    test_generator=TiledbPredictGenerator(ref_fasta=args.ref_fasta,
                                          batch_size=args.batch_size,
                                          tdb_indexer=args.tdb_indexer,
                                          tdb_partition_attribute_for_upsample=args.tdb_partition_attribute_for_upsample,
                                          tdb_partition_thresh_for_upsample=args.tdb_partition_thresh_for_upsample,
                                          upsample_ratio=upsample_ratio_predict,
                                          tdb_inputs=args.tdb_inputs,
                                          tdb_input_source_attribute=args.tdb_input_source_attribute,
                                          tdb_input_flank=args.tdb_input_flank,
                                          tdb_outputs=args.tdb_outputs,
                                          tdb_output_source_attribute=args.tdb_output_source_attribute,
                                          tdb_output_flank=args.tdb_output_flank,
                                          num_inputs=args.num_inputs,
                                          num_outputs=args.num_outputs,
                                          tdb_input_aggregation=args.tdb_input_aggregation,
                                          tdb_input_transformation=args.tdb_input_transformation,
                                          tdb_output_aggregation=args.tdb_output_aggregation,
                                          tdb_output_transformation=args.tdb_output_transformation,                                          
                                          tiledb_stride=args.tiledb_stride,
                                          chrom_sizes=args.chrom_sizes,
                                          chroms=args.predict_chroms)
    print("created TiledbPredictGenerator")    
    return test_generator 
def get_hdf5_predict_generator(args):
    global test_generator 
    test_generator=DataGenerator(index_path=args.index_data_path,
                                 input_path=args.input_data_path,
                                 output_path=args.output_data_path,
                                 index_tasks=args.index_tasks,
                                 num_inputs=args.num_inputs,
                                 num_outputs=args.num_outputs,
                                 ref_fasta=args.ref_fasta,
                                 batch_size=args.batch_size,
                                 add_revcomp=False,
                                 chroms_to_use=args.predict_chroms,
                                 expand_dims=args.expand_dims,
                                 tasks=args.tasks,
                                 shuffle=False,
                                 return_coords=True)
    return test_generator
def get_variant_predict_generator(args):
    global test_generator
    test_generator=SNPGenerator(args.allele_col,
                                args.flank,
                                index_path=args.index_data_path,
                                input_path=args.input_data_path,
                                output_path=args.output_data_path,
                                index_tasks=args.index_tasks,
                                num_inputs=args.num_inputs,
                                num_outputs=args.num_outputs,
                                ref_fasta=args.ref_fasta,
                                allele_col=args.ref_col,
                                batch_size=args.batch_size,
                                add_revcomp=False,
                                chroms_to_use=args.predict_chroms,
                                expand_dims=args.expand_dims,
                                tasks=args.tasks,
                                shuffle=False,
                                return_coords=True)

    return test_generator

def get_generator(args):
    if args.variant_bed is not None:
        return get_variant_predict_generator(args)
    elif args.tdb_indexer is not None:        
        return get_tiledb_predict_generator(args)
    else:
        return get_hdf5_predict_generator(args)

def predict_on_batch_wrapper(args,model,test_generator):
    num_batches=len(test_generator)
    processed=0
    try:
        with Pool(processes=args.threads,initializer=init_worker) as pool: 
            while (processed < num_batches):
                idset=range(processed,min([num_batches,processed+args.max_queue_size]))
                for result in pool.imap_unordered(get_batch_wrapper,idset):
                    X=result[0]
                    y=result[1]
                    coords=result[2]
                    idx=result[3]
                    processed+=1
                    if processed%10==0:
                        print(str(processed)+"/"+str(num_batches))
                    #get the model predictions            
                    preds=model.predict_on_batch(X)
                    if type(preds) is not list:
                        preds=[preds]
                    preds=[i.squeeze(axis=-1) for i in preds]
                    preds_dfs=[pd.DataFrame(cur_pred,index=coords) for cur_pred in preds]
                    label_queue.put(y)
                    pred_queue.put(preds_dfs)
                    
    except KeyboardInterrupt:
        #shutdown the pool
        pool.terminate()
        pool.join() 
        # Kill remaining child processes
        kill_child_processes(os.getpid())
        raise 
    except Exception as e:
        print(e)
        #shutdown the pool
        pool.terminate()
        pool.join()
        # Kill remaining child processes
        kill_child_processes(os.getpid())
        raise e
    print("finished with tiledb predictions!")
    label_queue.put("FINISHED")
    label_queue.close() 
    pred_queue.put("FINISHED")
    pred_queue.close() 
    return

def get_model(args):
    from kerasAC.metrics import recall, specificity, fpr, fnr, precision, f1    
    custom_objects={"recall":recall,
                    "sensitivity":recall,
                    "specificity":specificity,
                    "fpr":fpr,
                    "fnr":fnr,
                    "precision":precision,
                    "f1":f1,
                    "ambig_binary_crossentropy":ambig_binary_crossentropy,
                    "ambig_mean_absolute_error":ambig_mean_absolute_error,
                    "ambig_mean_squared_error":ambig_mean_squared_error}
    
    w1,w0=get_weights(args)
    if type(w1) in [np.ndarray, list]: 
        loss_function=get_weighted_binary_crossentropy(w0,w1)
        custom_objects["weighted_binary_crossentropy"]=loss_function
    if args.yaml!=None:
        from keras.models import model_from_yaml
        #load the model architecture from yaml
        yaml_string=open(args.yaml,'r').read()
        model=model_from_yaml(yaml_string,custom_objects=custom_objects) 
        #load the model weights
        model.load_weights(args.weights)
    elif args.json!=None:
        from keras.models import model_from_json
        #load the model architecture from json
        json_string=open(args.json,'r').read()
        model=model_from_json(json_string,custom_objects=custom_objects)
        model.load_weights(args.weights)
    elif args.model_hdf5!=None: 
        #load from the hdf5
        from keras.models import load_model
        model=load_model(args.model_hdf5,custom_objects=custom_objects)
    print("got model architecture")
    print("loaded model weights")   
    return model



def get_model_layer_functor(model,target_layer_idx):
    from keras import backend as K
    inp=model.input
    outputs=model.layers[target_layer_idx].output
    functor=K.function([inp], [outputs])
    return functor 

def get_layer_outputs(functor,X):
    return functor([X])

def predict(args):
    if type(args)==type({}):
        args=args_object_from_args_dict(args) 
    global pred_queue
    global label_queue
    
    pred_queue=Queue()
    label_queue=Queue()
    
    label_writer=Process(target=write_predictions,args=([args]))
    pred_writer=Process(target=write_labels,args=([args]))
    label_writer.start()
    pred_writer.start() 


    #get the generator
    test_generator=get_generator(args) 
    
    #get the model
    #if calibration is to be done, get the preactivation model 
    model=get_model(args)
    perform_calibration=args.calibrate_classification or args.calibrate_regression
    if perform_calibration==True:
        if args.calibrate_classification==True:
            print("getting logits")
            model=Model(inputs=model.input,
                               outputs=model.layers[-2].output)
        elif args.calibrate_regression==True:
            print("getting pre-relu outputs (preacts)")
            model=Model(inputs=model.input,
                        outputs=model.layers[-1].output)
            
    #call the predict_on_batch_wrapper
    predict_on_batch_wrapper(args,model,test_generator)

    #drain the queue
    try:
        while not label_queue.empty():
            print("draining the label Queue")
            time.sleep(2)
    except Exception as e:
        print(e)
    try:
        while not pred_queue.empty():
            print("draining the prediction Queue")
            time.sleep(2)
    except Exception as e:
        print(e)
    
    print("joining label writer") 
    label_writer.join()
    print("joining prediction writer") 
    pred_writer.join()


    #perform calibration, if specified
    if perform_calibration is True:
        print("calibrating")
        calibrate(args)
    
    
def main():
    args=parse_args()
    predict(args)


if __name__=="__main__":
    main()
    
