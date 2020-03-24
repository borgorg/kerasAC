import pdb 
import numpy as np ;
from keras.backend import int_shape
from sklearn.metrics import average_precision_score
from kerasAC.metrics import * 
from kerasAC.custom_losses import *

import keras;

#import the various keras layers 
from keras.layers import Dense,Activation,Dropout,Flatten,Reshape,Input, Concatenate, Cropping1D, Add
from keras.layers.core import Dropout, Reshape, Dense, Activation, Flatten
from keras.layers.convolutional import Conv1D
from keras.layers.pooling import GlobalMaxPooling1D,MaxPooling1D,GlobalAveragePooling1D
from keras.layers.normalization import BatchNormalization

from keras.optimizers import Adam
from keras.constraints import maxnorm;
from keras.regularizers import l1, l2    

from keras.models import Model

def getModelGivenModelOptionsAndWeightInits(args):
    #magic numbers# 
    filters=300
    n_dil_layers=6
    conv1_kernel_size=21
    profile_kernel_size=75
    control_smoothing=[1, 50]
    loss_weight=250
    
    #read in arguments
    seed=args.seed
    init_weights=args.init_weights 
    sequence_flank=args.tdb_input_flank[0]
    num_tasks=args.num_tasks
    
    seq_len=2*sequence_flank
    out_flank=args.tdb_output_flank[0]
    out_pred_len=2*out_flank
    print(seq_len)
    print(out_pred_len)
    #define inputs
    # The three inputs to BPNet
    inp = Input(shape=(seq_len, 4),name='sequence')    
    #bias_counts_input = Input(shape=(1, ), name="control_logcount")    
    #bias_profile_input = Input(shape=(out_pred_len, len(control_smoothing)), name="control_profile")
    # end inputs

    # first convolution without dilation
    first_conv = Conv1D(filters,
                        kernel_size=conv1_kernel_size,
                        padding='valid', 
                        activation='relu',
                        name='1st_conv')(inp)
    # 6 dilated convolutions with resnet-style additions
    # each layer receives the sum of feature maps 
    # from all previous layers
    res_layers = [(first_conv, '1stconv')] # on a quest to have meaninful
                                           # layer names
    layer_names = ['2nd', '3rd', '4th', '5th', '6th', '7th']
    for i in range(1, n_dil_layers + 1):
        if i == 1:
            res_layers_sum = first_conv
        else:
            res_layers_sum = Add(name='add_{}'.format(i))([l for l, _ in res_layers])

        # dilated convolution
        conv_layer_name = '{}conv'.format(layer_names[i-1])
        conv_output = Conv1D(filters, 
                             kernel_size=3, 
                             padding='valid',
                             activation='relu', 
                             dilation_rate=2**i,
                             name=conv_layer_name)(res_layers_sum)

        # get shape of latest layer and crop 
        # all other previous layers in the list to that size
        conv_output_shape =int_shape(conv_output)
        cropped_layers = []
        for lyr, name in res_layers:
            lyr_shape =int_shape(lyr)
            cropsize = int(lyr_shape[1]/2) - int(conv_output_shape[1]/2)
            lyr_name = '{}-crop_{}th_dconv'.format(name.split('-')[0], i)
            cropped_layers.append((Cropping1D(cropsize,
                                              name=lyr_name)(lyr),
                                  lyr_name))
        
        # append to the list of previous layers
        cropped_layers.append((conv_output, conv_layer_name))
        res_layers = cropped_layers

    # the final output from the 6 dilated convolutions 
    # with resnet-style connections
    combined_conv = Add(name='combined_conv')([l for l, _ in res_layers])

    # Branch 1. Profile prediction
    # Step 1.1 - 1D convolution with a very large kernel
    profile_out_prebias = Conv1D(filters=num_tasks,
                                 kernel_size=profile_kernel_size,
                                 padding='valid',
                                 name='profile_out_prebias')(combined_conv)
    # Step 1.2 - Crop to match size of the required output size, a minimum
    #            difference of 346 is required between input seq len and ouput len
    profile_out_prebias_shape =int_shape(profile_out_prebias)
    cropsize = int(profile_out_prebias_shape[1]/2)-int(out_pred_len/2)
    profile_out_prebias = Cropping1D(cropsize,
                                     name='prof_out_crop2match_output')(profile_out_prebias)
    # Step 1.3 - concatenate with the control profile 
    #concat_pop_bpi = Concatenate([profile_out_prebias,
    #                              bias_profile_input],
    #                             name="concat_with_bias_prof",
    #                             axis=-1)

    # Step 1.4 - Final 1x1 convolution
    #profile_out = Conv1D(filters=num_tasks,
    #                     kernel_size=1,
    #                     name="profile_predictions")(concat_pop_bpi)
    profile_out = Conv1D(filters=num_tasks,
                         kernel_size=1,
                         name="profile_predictions")(profile_out_prebias)
    # Branch 2. Counts prediction
    # Step 2.1 - Global average pooling along the "length", the result
    #            size is same as "filters" parameter to the BPNet function
    #gap_combined_conv = GlobalAveragePooling1D(name='gap')(combined_conv) # acronym - gapcc
    gap_combined_conv = GlobalAveragePooling1D(name='gap')(profile_out_prebias) # acronym - gapcc
    # Step 2.2 Concatenate the output of GAP with bias counts
    #concat_gapcc_bci = Concatenate([gap_combined_conv, 
    #                                bias_counts_input],
    #                               name="concat_with_bias_cnts",
    #                               axis=-1)
    
    # Step 2.3 Dense layer to predict final counts
    #count_out = Dense(num_tasks, name="logcount_predictions")(concat_gapcc_bci)
    count_out = Dense(num_tasks, name="logcount_predictions")(gap_combined_conv)

    # instantiate keras Model with inputs and outputs
    #model = Model(inputs=[inp, bias_counts_input, bias_profile_input],
    #                     outputs=[profile_out, count_out])
    model=Model(inputs=[inp],outputs=[profile_out,
                                     count_out])
    print("got model") 
    model.compile(optimizer=Adam(),
                    loss=[MultichannelMultinomialNLL(1),'mse'],
                    loss_weights=[loss_weight,1])
    print("compiled model")
    return model 


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description="view model arch")
    parser.add_argument("--seed",type=int,default=1234)
    parser.add_argument("--init_weights",default=None)
    parser.add_argument("--tdb_input_flank",nargs="+",default=[673])
    parser.add_argument("--tdb_output_flank",nargs="+",default=[500])
    parser.add_argument("--num_tasks",type=int,default=1)
    args=parser.parse_args()
    model=getModelGivenModelOptionsAndWeightInits(args)
    print(model.summary())
    pdb.set_trace() 
    
