#!/bin/bash
cd ../tools
python3 train_net.py \
    --dataset_name          ertesito2-layout \
    --json_annotation_train ../ertesito2/train.json \
    --image_path_train      ../ertesito2/images \
    --json_annotation_val   ../ertesito2/test.json \
    --image_path_val        ../ertesito2/images \
    --resume \
    --config-file           ../configs/ertesito2/fast_rcnn_R_50_FPN_3x.yaml \
    OUTPUT_DIR  ../outputs/ertesito2/fast_rcnn_R_50_FPN_3x/ \
    SOLVER.IMS_PER_BATCH 2
     