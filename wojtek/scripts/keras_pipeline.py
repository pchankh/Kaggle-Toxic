import gc
import glob
import os
import shutil
import time

import numpy as np
import pandas as pd
import tensorflow as tf
import tflearn
from importance_sampling.training import ImportanceTraining
from keras import backend as K
from keras import objectives
from keras.callbacks import Callback, CSVLogger, ModelCheckpoint, TensorBoard
from keras.layers import Layer
from keras.models import load_model
from keras_attention_context import AttentionWithContext
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold, StratifiedKFold, train_test_split

# from .utils import copytree


class KerasPipeline(object):

    """Keras Pipeline for various training & prediction methods.

    # Arguments
        model_name: (String) Name of model based on .py file with models definitions.
        predict_test: (Boolean), whether to predict on test set.
        model_callbacks: (List), list of callbacks for the model.
        number_epochs: (Int), number of epochs to train the model for.
        batch_size: (Int), batch size for model training and prediction.

        seed: (Int), random seed number for splits.
        shuffle: (Boolean), whether to shuffle data during training & data split.
        verbose: (Boolean,) whether to print information about the run.

        run_save_name: (String), name of run used during checkpoint & run statistics
            saving.
        load_keras_model: (Boolean), whether to load already trained model.
        save_model: (Boolean), whether to save model checkpoints, by default in src_dir + 'checkpoints/'.
        save_history: (Boolean), whether to save history of a model in CSV file.
        save_statistics: (Boolean), whether to save run statistics.
        output_statistics: (Boolean), whether to show run statistics.

        src_dir: (String), working directory for model training & default checkpoints location.
        full_train_dir: (String), directory containing full (original) training dataset.
        train_dir: (String), directory containing training split data.
        valid_dir: (String), directory containing validation split data.
        test_dir: (String), directory containing test split data.

        train_datagen: (ImageDataGenerator object), specifying data augmentation
            parameters for training set.
        valid_datagen: (ImageDataGenerator object), specifying data augmentation
            parameters for validation set.
        test_datagen: (ImageDataGenerator object), specifying data augmentation
            parameters for training set.
        number_test_augmentations: (Int), number of data augmentations to perform
            during test data prediction.

        image_size: (Tuple), containing image width and height, e.g. (299, 299)
        classes: (List), list of names of classes in the data,
            e.g. ['Shark', 'Tuna', 'Whale']

        number_train_samples: (Int), number of samples in training set,
            given to Keras generator
        number_validation_samples: (Int), number of samples in validation set,
            given to Keras generator
        number_test_samples: (Int), number of samples in test set,
            given to Keras generator
    """

    def __init__(self,
                 model_name,
                 predict_test=False,
                 model_callbacks=None,
                 number_epochs=1,
                 batch_size=1,
                 seed=None,
                 shuffle=True,
                 verbose=True,
                 run_save_name=None,
                 load_keras_model=False,
                 save_model=True,
                 save_history=False,
                 save_statistics=False,
                 output_statistics=True,
                 src_dir=None,
                 full_train_dir=None,
                 train_dir=None,
                 valid_dir=None,
                 test_dir=None,
                 train_datagen=None,
                 valid_datagen=None,
                 test_datagen=None,
                 number_test_augmentations=0,
                 image_size=None,
                 classes=None,
                 number_train_samples=0,
                 number_validation_samples=0,
                 number_test_samples=0,
                 ):

        self.model_name = model_name
        self.predict_test = predict_test
        self.model_callbacks = model_callbacks if model_callbacks is not None else []
        self.number_epochs = number_epochs
        self.batch_size = batch_size
        self.seed = seed
        self.shuffle = shuffle
        self.verbose = verbose

        self.run_save_name = run_save_name
        self.load_keras_model = load_keras_model
        self.save_model = save_model
        self.save_history = save_history
        self.save_statistics = save_statistics if run_save_name is not None else False
        self.output_statistics = output_statistics

        self.src_dir = src_dir if src_dir is not None else os.getcwd()
        self.full_train_dir = full_train_dir
        self.train_dir = train_dir
        self.valid_dir = valid_dir
        self.test_dir = test_dir

        self.train_datagen = train_datagen
        self.valid_datagen = valid_datagen
        self.test_datagen = test_datagen
        self.number_test_augmentations = number_test_augmentations

        self.image_size = image_size
        self.classes = classes if classes is not None else []
        self.number_train_samples = number_train_samples
        self.number_validation_samples = number_validation_samples
        self.number_test_samples = number_test_samples

        self.oof_train = None
        self.oof_test = None

        self.i = 1
        self.start_time = time.time()
        self.checkpoints_dst = self.src_dir + '/checkpoints/'

        self.predictions_valid = []
        self.predictions_test = []
        self.loss_history = []
        self.min_losses = []

    def bag_run(self,
                X_train, y_train,
                X_valid=None, y_valid=None,
                X_test=None, y_test=None,
                model_params=None,
                model_callbacks=None,
                n_bags=2,
                split_size=0.2,
                user_split=False,
                index_number=None,
                flow_augment=False,
                importance_training=False,
                save_oof=False,
                ):
        """Bagging run.

        # Arguments
            X_train: (numpy array), training set.
            y_train: (numpy array), training set labels.
            X_valid: (numpy array), validation set.
            y_valid: (numpy array), validation set labels.
            X_test: (numpy array), test set.
            y_test: (numpy array), test set labels.
            model_params: (Dict), dictionary of model parameters.
            n_bags: (Int), number of bags used in training.
            split_size: (Float), size of validation split in percentage of training set size.
            user_split: (Boolean), whether validation set is provided by user, not created.
            index_number: (Int), index specifying from which bag should training or prediction be started.
            flow_augment: (Boolean), whether to use data augmentation during test and prediction.

        # Returns
            model: (Keras model), trained model for last bag.
            predictions_valid: (numpy array), array for validation set predictions.
            if predict_test additionally:
                predictions_test: (numpy array), array for test set predictions.
        """

        print('Running: {}'.format(self.run_save_name))

        if index_number is not None:
            self.i = index_number

        if self.save_statistics or self.save_model:
            os.makedirs('{}{}'.format(
                self.checkpoints_dst, self.run_save_name), exist_ok=True)

        for bag in range(n_bags):
            print('Training on bag:', self.i, '\n')
            model = self.model_name(model_params)
            print(model.summary())

            training_callbacks = model_callbacks.copy()
            if self.save_model:
                training_callbacks = self.callbacks_append_checkpoint(training_callbacks,
                                                                      'bag')
            if self.save_history:
                training_callbacks = self.callbacks_append_logger(training_callbacks,
                                                                  'bag')

            if X_valid is not None and y_valid is not None and user_split:
                print('Validating on subset of data specified by user.')

                X_valid_ = X_valid
                y_valid_ = y_valid

                RocAuc = RocAucEvaluation(
                    validation_data=(X_valid, y_valid_), interval=1)
                training_callbacks.append(RocAuc)

                if self.load_keras_model:
                    model = self.load_trained_model('bag')
                else:
                    if flow_augment:
                        print('Training with data augmentation.')
                        if importance_training:
                            print('Training with importance sampling.')
                            history = ImportanceTraining(model, k=1.0, smooth=0.5).fit_generator(
                                self.train_datagen.flow(
                                    X_train, y_train, batch_size=self.batch_size),
                                steps_per_epoch=X_train.shape[0] /
                                self.batch_size,
                                epochs=self.number_epochs,
                                validation_data=self.valid_datagen.flow(
                                    X_valid_, y_valid_, batch_size=self.batch_size,
                                    shuffle=False),
                                validation_steps=X_valid_.shape[0] /
                                self.batch_size,
                                callbacks=training_callbacks)
                        else:
                            history = model.fit_generator(
                                self.train_datagen.flow(
                                    X_train, y_train, batch_size=self.batch_size),
                                steps_per_epoch=X_train.shape[0] /
                                self.batch_size,
                                epochs=self.number_epochs,
                                validation_data=self.valid_datagen.flow(
                                    X_valid_, y_valid_, batch_size=self.batch_size,
                                    shuffle=False),
                                validation_steps=X_valid_.shape[0] /
                                self.batch_size,
                                callbacks=training_callbacks)
                    else:
                        if importance_training:
                            print('Training with importance sampling.')
                            history = ImportanceTraining(model, k=1.0, smooth=0.5).fit(X_train, y_train, verbose=self.verbose,
                                                                                       batch_size=self.batch_size, epochs=self.number_epochs,
                                                                                       validation_data=(
                                                                                           X_valid_, y_valid_),
                                                                                       callbacks=training_callbacks)
                        else:
                            history = model.fit(X_train, y_train, verbose=self.verbose,
                                                batch_size=self.batch_size, epochs=self.number_epochs,
                                                validation_data=(
                                                    X_valid_, y_valid_),
                                                callbacks=training_callbacks)

            else:
                if self.seed:
                    print('Splitting data - validation split size: {}, split seed: {}'.format(
                        split_size, self.seed))
                else:
                    print('Splitting data - validation split size: {}, seed not set.'.format(
                        split_size))

                X_tr, X_valid_, y_tr, y_valid_ = train_test_split(
                    X_train, y_train, test_size=split_size, random_state=self.seed)

                RocAuc = RocAucEvaluation2(
                    validation_data=(X_valid_, y_valid_), evalLogFilePath='{0}{1}/{1}_ROC_AUC.txt'.format(
                        self.checkpoints_dst, self.run_save_name), interval=1)
                training_callbacks.append(RocAuc)

                if self.load_keras_model:
                    model = self.load_trained_model('bag')
                else:
                    if flow_augment:
                        print('Training with data augmentation.')
                        if importance_training:
                            print('Training with importance sampling.')
                            history = ImportanceTraining(model, k=1.0, smooth=0.5).fit_generator(
                                self.train_datagen.flow(
                                    X_tr, y_tr, batch_size=self.batch_size),
                                steps_per_epoch=X_tr.shape[0] /
                                self.batch_size,
                                epochs=self.number_epochs,
                                validation_data=self.valid_datagen.flow(
                                    X_valid_, y_valid_, batch_size=self.batch_size,
                                    shuffle=False),
                                validation_steps=X_valid_.shape[0] /
                                self.batch_size,
                                callbacks=training_callbacks)
                        else:
                            history = model.fit_generator(
                                self.train_datagen.flow(
                                    X_tr, y_tr, batch_size=self.batch_size),
                                steps_per_epoch=X_tr.shape[0] /
                                self.batch_size,
                                epochs=self.number_epochs,
                                validation_data=self.valid_datagen.flow(
                                    X_valid_, y_valid_, batch_size=self.batch_size,
                                    shuffle=False),
                                validation_steps=X_valid_.shape[0] /
                                self.batch_size,
                                callbacks=training_callbacks)
                    else:
                        if importance_training:
                            print('Training with importance sampling.')
                            history = ImportanceTraining(model, k=1.0, smooth=0.5).fit(X_tr, y_tr, verbose=self.verbose,
                                                                                       batch_size=self.batch_size, epochs=self.number_epochs,
                                                                                       validation_data=(
                                                                                           X_valid_, y_valid_),
                                                                                       callbacks=training_callbacks)
                        else:
                            history = model.fit(X_tr, y_tr, verbose=self.verbose,
                                                batch_size=self.batch_size, epochs=self.number_epochs,
                                                validation_data=(
                                                    X_valid_, y_valid_),
                                                callbacks=training_callbacks)

            if not self.load_keras_model:
                validation_loss = history.history['val_loss']
                self.loss_history.append(validation_loss)
                self.min_losses.append(np.min(validation_loss))
                if self.output_statistics:
                    self.output_run_statistics('bag')

            print('Predicting on validation data.')
            self.predictions_valid.append(model.predict(
                X_valid_, batch_size=self.batch_size))
            if self.verbose:
                print('Validation split - standard deviation for original target values: {} \n \
                 for predicted target values: {} \n \n'.format(
                    np.std(y_valid_), np.std(self.predictions_valid)))

            if self.predict_test and X_test is not None:
                print('Predicting on test data.')
                if flow_augment:
                    self.predictions_test.append(
                        self.flow_predict_test_augment(X_test, model))
                else:
                    self.predictions_test.append(model.predict(
                        X_test, batch_size=self.batch_size))

            self.i += 1

        if self.predict_test and save_oof:
            if self.load_keras_model:
                self.run_save_name = '{}_fromCheck'.format(self.run_save_name)
            pd.to_pickle(np.array(self.predictions_valid), 'predictions/valid/{}_{:.5f}.pkl'.format(
                self.run_save_name, np.array(self.min_losses).mean(axis=0)))
            pd.to_pickle(np.array(self.predictions_test), 'predictions/test/{}_{:.5f}.pkl'.format(
                self.run_save_name, np.array(self.min_losses).mean(axis=0)))

        if self.predict_test and X_test is not None:
            return model, np.array(self.predictions_valid), np.array(self.predictions_test)
        return model, np.array(self.predictions_valid)

    def full_train_run(self,
                       X_train, y_train,
                       X_test=None, y_test=None,
                       model_params=None,
                       model_callbacks=None,
                       n_bags=2,
                       index_number=None,
                       flow_augment=False,
                       importance_training=False,
                       ):
        """Full training run, without validation set.

        # Arguments
            X_train: (numpy array), training set.
            y_train: (numpy array), training set labels.
            X_test: (numpy array), test set.
            y_test: (numpy array), test set labels.
            model_params: (Dict), dictionary of model parameters.
            n_bags: (Int), number of bags used in training.
            split_size: (Float), size of validation split in percentage of training set size.
            user_split: (Boolean), whether validation set is provided by user, not created.
            index_number: (Int), index specifying from which bag should training or prediction be started.
            flow_augment: (Boolean), whether to use data augmentation during test and prediction.

        # Returns
            model: (Keras model), trained model for last bag.
            predictions_valid: (numpy array), array for validation set predictions.
            if predict_test additionally:
                predictions_test: (numpy array), array for test set predictions.
        """

        print('Running: {}'.format(self.run_save_name))

        if index_number is not None:
            self.i = index_number

        if self.save_statistics:
            os.makedirs('{}{}'.format(
                self.checkpoints_dst, self.run_save_name), exist_ok=True)

        for bag in range(n_bags):
            print('Full training run, current bag:', self.i, '\n')
            model = self.model_name(model_params)

            training_callbacks = model_callbacks.copy()
            if self.save_model:
                training_callbacks = self.callbacks_append_checkpoint(training_callbacks,
                                                                      'fulltrain', full_training=True)
            if self.save_history:
                training_callbacks = self.callbacks_append_logger(training_callbacks,
                                                                  'fulltrain')

            if self.load_keras_model:
                model = self.load_trained_model('fulltrain')
            else:
                if flow_augment:
                    print('Training with data augmentation.')
                    if importance_training:
                        print('Training with importance sampling.')
                        history = ImportanceTraining(model, k=1.0, smooth=0.5).fit_generator(
                            self.train_datagen.flow(
                                X_train, y_train, batch_size=self.batch_size),
                            steps_per_epoch=X_train.shape[0] /
                            self.batch_size,
                            epochs=self.number_epochs,
                            callbacks=training_callbacks)
                    else:
                        history = model.fit_generator(
                            self.train_datagen.flow(
                                X_train, y_train, batch_size=self.batch_size),
                            steps_per_epoch=X_train.shape[0] / self.batch_size,
                            epochs=self.number_epochs,
                            callbacks=training_callbacks)
                else:
                    if importance_training:
                        print('Training with importance sampling.')
                        history = ImportanceTraining(model, k=1.0, smooth=0.5).fit(X_train, y_train, verbose=self.verbose,
                                                                                   batch_size=self.batch_size, epochs=self.number_epochs,
                                                                                   callbacks=training_callbacks)
                    else:
                        history = model.fit(X_train, y_train, verbose=self.verbose,
                                            batch_size=self.batch_size, epochs=self.number_epochs,
                                            callbacks=training_callbacks)

            if self.predict_test and X_test is not None:
                print('Predicting on test data.')
                if flow_augment:
                    self.predictions_test.append(
                        self.flow_predict_test_augment(X_test, model))
                else:
                    self.predictions_test.append(model.predict(
                        X_test, batch_size=self.batch_size))

            self.i += 1

        if self.predict_test and X_test is not None:
            return model, np.array(self.predictions_test)
        return model

    def kfold_run(self,
                  X_train, y_train,
                  X_test=None, y_test=None,
                  model_params=None,
                  model_callbacks=None,
                  n_folds=5,
                  stratify=False,
                  index_number=None,
                  flow_augment=False,
                  flow_augment_test=False,
                  save_oof=False,
                  importance_training=False,
                  MLP_array=None,
                  ):
        """KFold/StratifiedKFold run.

        # Arguments
            X_train: (numpy array), training set.
            y_train: (numpy array), training set labels.
            X_test: (numpy array), test set.
            y_test: (numpy array), test set labels.
            model_params: (Dict), dictionary of model parameters.
            n_folds: (Int), number of folds used in training.
            stratify: (Boolean), whether fold split should be stratified according to labels distribution.
            index_number: (Int), index specifying from which bag should training or prediction be started.
            flow_augment: (Boolean), whether to use data augmentation during test and prediction.
            flow_augment_test: (Boolean), whether to use data augmentation during test and prediction.
            save_oof: (Boolean), whether to automatically save oof predictions.
                Assumes oof/train and oof/test folders in source directory.

        # Returns
            model: (Keras model), trained model for last fold.
            oof_train: (numpy array), array with out-of-fold training set predictions.
            if predict_test additionally:
                oof_test: (numpy array), array with out-of-fold test set predictions.
        """

        print('Running: {}'.format(self.run_save_name))

        oof_index = 0
        if index_number is not None:
            self.i = index_number
            oof_index = index_number - 1

        if self.save_statistics or self.save_model:
            os.makedirs('{}{}'.format(
                self.checkpoints_dst, self.run_save_name), exist_ok=True)

        if len(y_train.shape) == 1:
            y_train = y_train.reshape((y_train.shape[0], 1))

        self.oof_train = np.zeros(y_train.shape + (1,))
        print('OOF train predictions shape: {}'.format(self.oof_train.shape))

        if X_test is not None:
            self.oof_test = np.zeros(
                (X_test.shape[0],) + y_train.shape[1:] + (n_folds,))
            print('OOF test predictions shape: {}'.format(self.oof_test.shape))

        if MLP_array is not None:
            angle_train = MLP_array[:X_train.shape[0]]
            angle_test = MLP_array[X_train.shape[0]:]
            X_test = [X_test, angle_test]

        if stratify and self.oof_train.shape[-2] != 1:
            print(
                'To use StratifiedKFold please provide categorically encoded labels, not One-Hot encoded. \
                \n Reversing OH encoding now.')
            y_train_split = pd.DataFrame(y_train).sum(axis=1).values
            print('Labels after reversed encoding:', y_train_split[:10])
            kf = StratifiedKFold(
                n_splits=n_folds, shuffle=self.shuffle, random_state=self.seed)
        else:
            kf = KFold(
                n_splits=n_folds, shuffle=self.shuffle, random_state=self.seed)
            y_train_split = y_train

        for train_index, test_index in kf.split(X_train, y_train_split):
            print('Training on fold:', self.i, '\n')

            X_tr, X_val = X_train[train_index], X_train[test_index]
            y_tr, y_val = y_train[train_index], y_train[test_index]

            if MLP_array is not None:
                angle_tr, angle_val = angle_train[train_index], angle_train[test_index]
                X_tr, X_val = [X_tr, angle_tr], [X_val, angle_val]

            self.loss_history = []

            if self.load_keras_model:
                if importance_training:
                    pass
                model = self.load_trained_model('fold')
            else:
                model = self.model_name(model_params)
                print(model.summary())

                training_callbacks = model_callbacks.copy()
                if self.save_model:
                    training_callbacks = self.callbacks_append_checkpoint(training_callbacks,
                                                                          'fold')
                if self.save_history:
                    training_callbacks = self.callbacks_append_logger(training_callbacks,
                                                                      'fold')

                # RocAuc = RocAucEvaluation(
                #     validation_data=(X_val, y_val), interval=1)
                RocAuc = RocAucEvaluation2(
                    validation_data=(X_val, y_val), evalLogFilePath='{0}{1}/{1}_ROC_AUC.txt'.format(
                        self.checkpoints_dst, self.run_save_name), interval=1)
                training_callbacks.append(RocAuc)

                if flow_augment:
                    print('Training with data augmentation.')
                    if importance_training:
                        print('Training with importance sampling.')
                        history = ImportanceTraining(model, k=1.0, smooth=0.5).fit_generator(
                            self.train_datagen.flow(
                                X_tr, y_tr, batch_size=self.batch_size),
                            steps_per_epoch=X_tr.shape[0] /
                            self.batch_size,
                            epochs=self.number_epochs,
                            validation_data=self.valid_datagen.flow(
                                X_val, y_val, batch_size=self.batch_size,
                                shuffle=False),
                            validation_steps=X_val.shape[0] /
                            self.batch_size,
                            callbacks=training_callbacks)
                    else:
                        history = model.fit_generator(
                            self.train_datagen.flow(
                                X_tr, y_tr, batch_size=self.batch_size),
                            steps_per_epoch=X_tr.shape[0] /
                            self.batch_size,
                            epochs=self.number_epochs,
                            validation_data=self.valid_datagen.flow(
                                X_val, y_val, batch_size=self.batch_size,
                                shuffle=False),
                            validation_steps=X_val.shape[0] /
                            self.batch_size,
                            callbacks=training_callbacks)
                else:
                    if importance_training:
                        print('Training with importance sampling.')
                        history = ImportanceTraining(model, k=1.0, smooth=0.5).fit(X_tr, y_tr, verbose=self.verbose,
                                                                                   batch_size=self.batch_size, epochs=self.number_epochs,
                                                                                   validation_data=(
                                                                                       X_val, y_val),
                                                                                   callbacks=training_callbacks)
                    else:
                        history = model.fit(X_tr, y_tr, verbose=self.verbose,
                                            batch_size=self.batch_size, epochs=self.number_epochs,
                                            validation_data=(
                                                X_val, y_val),
                                            callbacks=training_callbacks)

            if not self.load_keras_model:
                validation_loss = history.history['val_loss']
                self.loss_history.append(validation_loss)
                self.min_losses.append(np.min(validation_loss))
                if self.output_statistics:
                    self.output_run_statistics('fold')

            print('Predicting on validation data.')
            self.oof_train[test_index, :, 0] = model.predict(
                X_val, batch_size=self.batch_size)
            if self.verbose:
                print('Validation split - standard deviation for original target values: {} \n \
                for predicted target values: {} \n \n'.format(
                    np.std(y_val), np.std(self.oof_train[test_index, :])))

            if self.predict_test and X_test is not None:
                print('Predicting on test data.')
                if flow_augment_test:
                    print('With augmentation.')
                    self.oof_test[:, :, oof_index] = self.flow_predict_test_augment(
                        X_test, model)
                else:
                    self.oof_test[:, :, oof_index] = model.predict(
                        X_test, batch_size=self.batch_size)
                oof_index += 1

            self.i += 1

        print('Mean loss for current KFold run:',
              np.array(self.min_losses).mean(axis=0))
        print((np.array(self.oof_train).shape))
        print((np.array(self.oof_test).shape))

        if self.predict_test and save_oof:
            if self.load_keras_model:
                self.run_save_name = '{}_fromCheck'.format(self.run_save_name)
            pd.to_pickle(np.array(self.oof_train), 'oof/train/{}_{:.5f}.pkl'.format(
                self.run_save_name, np.array(self.min_losses).mean(axis=0)))
            pd.to_pickle(np.array(self.oof_test), 'oof/test/{}_{:.5f}.pkl'.format(
                self.run_save_name, np.array(self.min_losses).mean(axis=0)))

        if self.predict_test and X_test is not None:
            return model, np.array(self.oof_train), np.array(self.oof_test)
        return model, np.array(self.oof_train)

    def bagged_kfold_run(self,
                         X_train, y_train,
                         X_test=None, y_test=None,
                         model_params=None,
                         model_callbacks=None,
                         n_bags=2,
                         n_folds=5,
                         stratify=False,
                         index_number=None,
                         flow_augment=False,
                         flow_augment_test=False,
                         save_oof=False,
                         importance_training=False,
                         ):
        """Bagged KFold/StratifiedKFold run.

        # Arguments
            X_train: (numpy array), training set.
            y_train: (numpy array), training set labels.
            X_test: (numpy array), test set.
            y_test: (numpy array), test set labels.
            model_params: (Dict), dictionary of model parameters.
            n_bags: (Int), number of bags used in training.
            n_folds: (Int), number of folds used in training.
            stratify: (Boolean), whether fold split should be stratified according to labels distribution.
            index_number: (Int), index specifying from which bag should training or prediction be started.
            flow_augment: (Boolean), whether to use data augmentation during training.
            flow_augment_test: (Boolean), whether to use data augmentation during test and prediction.
            save_oof: (Boolean), whether to automatically save oof predictions.
                Assumes oof/train and oof/test folders in source directory.

        # Returns
            model: (Keras model), trained model for last fold.
            oof_train: (numpy array), array with out-of-fold training set predictions.
            if predict_test additionally:
                oof_test: (numpy array), array with out-of-fold test set predictions.
        """

        bag_number = 0
        print('Running: {}'.format(self.run_save_name))

        if self.save_statistics:
            os.makedirs('{}{}'.format(
                self.checkpoints_dst, self.run_save_name), exist_ok=True)

        if len(y_train.shape) == 1:
            y_train = y_train.reshape((y_train.shape[0], 1))

        self.oof_train = np.zeros(y_train.shape + (1,) + (n_bags,))
        print('OOF train predictions shape: {}'.format(self.oof_train.shape))

        if X_test is not None:
            self.oof_test = np.zeros(
                (X_test.shape[0],) + y_train.shape[1:] + (n_folds,) + (n_bags,))
            print('OOF test predictions shape: {}'.format(self.oof_test.shape))

        for bag in range(n_bags):

            self.i = index_number
            oof_index = 0

            if stratify and self.oof_train.shape[-3] != 1:
                print(
                    'To use StratifiedKFold please provide categorically encoded labels, not One-Hot encoded. \
                    \n Reversing OH encoding now.')
                y_train_split = pd.DataFrame(y_train).idxmax(axis=1).values
                print('Labels after reversed encoding:', y_train_split[:10])
                kf = StratifiedKFold(
                    n_splits=n_folds, shuffle=self.shuffle, random_state=self.seed)
            else:
                kf = KFold(
                    n_splits=n_folds, shuffle=self.shuffle, random_state=self.seed)
                y_train_split = y_train

            for train_index, test_index in kf.split(X_train, y_train_split):
                print('Training on bag: {}, fold: {} \n'.format(bag_number, self.i))

                X_tr, X_val = X_train[train_index], X_train[test_index]
                y_tr, y_val = y_train[train_index], y_train[test_index]

                self.loss_history = []

                if self.load_keras_model:
                    if importance_training:
                        pass
                    model = self.load_trained_model('bag_{}_fold'.format(bag))
                else:
                    model = self.model_name(model_params)

                    training_callbacks = model_callbacks.copy()
                    if self.save_model:
                        training_callbacks = self.callbacks_append_checkpoint(training_callbacks,
                                                                              'bag_{}_fold'.format(bag))
                    if self.save_history:
                        training_callbacks = self.callbacks_append_logger(training_callbacks,
                                                                          'bag_{}_fold'.format(bag))

                    if flow_augment:
                        print('Training with data augmentation.')
                        if importance_training:
                            print('Training with importance sampling.')
                            history = ImportanceTraining(model, k=1.0, smooth=0.5).fit_generator(
                                self.train_datagen.flow(
                                    X_tr, y_tr, batch_size=self.batch_size),
                                steps_per_epoch=X_tr.shape[0] /
                                self.batch_size,
                                epochs=self.number_epochs,
                                validation_data=self.valid_datagen.flow(
                                    X_val, y_val, batch_size=self.batch_size,
                                    shuffle=False),
                                validation_steps=X_val.shape[0] /
                                self.batch_size,
                                callbacks=training_callbacks)
                        else:
                            history = model.fit_generator(
                                self.train_datagen.flow(
                                    X_tr, y_tr, batch_size=self.batch_size),
                                steps_per_epoch=X_tr.shape[0] /
                                self.batch_size,
                                epochs=self.number_epochs,
                                validation_data=self.valid_datagen.flow(
                                    X_val, y_val, batch_size=self.batch_size,
                                    shuffle=False),
                                validation_steps=X_val.shape[0] /
                                self.batch_size,
                                callbacks=training_callbacks)
                    else:
                        if importance_training:
                            print('Training with importance sampling.')
                            history = ImportanceTraining(model, k=1.0, smooth=0.5).fit(X_tr, y_tr, verbose=self.verbose,
                                                                                       batch_size=self.batch_size, epochs=self.number_epochs,
                                                                                       validation_data=(
                                                                                           X_val, y_val),
                                                                                       callbacks=training_callbacks)
                        else:
                            history = model.fit(X_tr, y_tr, verbose=self.verbose,
                                                batch_size=self.batch_size, epochs=self.number_epochs,
                                                validation_data=(
                                                    X_val, y_val),
                                                callbacks=training_callbacks)

                if not self.load_keras_model:
                    validation_loss = history.history['val_loss']
                    self.loss_history.append(validation_loss)
                    self.min_losses.append(np.min(validation_loss))
                    if self.output_statistics:
                        self.output_run_statistics('bag_{}_fold'.format(bag))

                print('Predicting on validation data.')
                self.oof_train[test_index, :, 0, bag_number] = model.predict(
                    X_val, batch_size=self.batch_size)
                if self.verbose:
                    print('Validation split - standard deviation for original target values: {} \n \
                    for predicted target values: {} \n \n'.format(
                        np.std(y_val), np.std(self.oof_train[test_index, :, 0, bag_number])))

                if self.predict_test and X_test is not None:
                    print('Predicting on test data.')
                    if flow_augment_test:
                        self.oof_test[:, :, oof_index, bag_number] = self.flow_predict_test_augment(
                            X_test, model)
                    else:
                        self.oof_test[:, :, oof_index, bag_number] = model.predict(
                            X_test, batch_size=self.batch_size)
                    oof_index += 1

                self.i += 1
            bag_number += 1

        print('Mean loss for current bagged KFold run:',
              np.array(self.min_losses).mean(axis=0))
        if self.predict_test and save_oof:
            pd.to_pickle(np.array(self.oof_train), 'oof/train/{}_{:.5f}.pkl'.format(
                self.run_save_name, np.array(self.min_losses).mean(axis=0)))
            pd.to_pickle(np.array(self.oof_test), 'oof/test/{}_{:.5f}.pkl'.format(
                self.run_save_name, np.array(self.min_losses).mean(axis=0)))

        if self.predict_test and X_test is not None:
            return model, np.array(self.oof_train), np.array(self.oof_test)
        return model, np.array(self.oof_train)

    def directory_bag_flow_run(self,
                               model_params=None,
                               model_callbacks=None,
                               n_bags=2,
                               split_size=0.2,
                               split_every_bag=False,
                               index_number=None):
        """Bagging run using .flow_from_directory for loading data from directly from disk.

        # Arguments
            model_params: (Dict), dictionary of model parameters.
            n_bags: (Int), number of bags used in training.
            split_size: (Float), size of validation split in percentage of training set size.
            split_every_bag: (Boolean), whether to create random validation split for every bag.
            index_number: (Int), index specifying from which bag should training or prediction be started.

        # Returns
            model: (Keras model), trained model for last bag.
            if predict_test additionally:
                predictions_test: (numpy array), array for test set predictions.
                test_image_names: (List), list with test filenames.
        """

        print('Running: {}'.format(self.run_save_name))

        if index_number is not None:
            self.i = index_number

        if self.save_statistics:
            os.makedirs('{}{}'.format(
                self.checkpoints_dst, self.run_save_name), exist_ok=True)

        for bag in range(n_bags):
            print('Training on bag:', self.i, '\n')
            model = self.model_name(model_params)

            training_callbacks = model_callbacks.copy()
            if self.save_model:
                training_callbacks = self.callbacks_append_checkpoint(training_callbacks,
                                                                      'bag_dir')
            if self.save_history:
                training_callbacks = self.callbacks_append_logger(training_callbacks,
                                                                  'bag_dir')

            if split_every_bag:
                self.perform_random_validation_split(split_size)

            train_generator = self.train_datagen.flow_from_directory(
                self.train_dir,
                target_size=self.image_size,
                batch_size=self.batch_size,
                seed=self.seed,
                shuffle=self.shuffle,
                classes=self.classes,
                class_mode='categorical')

            validation_generator = self.valid_datagen.flow_from_directory(
                self.valid_dir,
                target_size=self.image_size,
                batch_size=self.batch_size,
                seed=self.seed,
                shuffle=self.shuffle,
                classes=self.classes,
                class_mode='categorical')

            if self.load_keras_model:
                pass
            else:
                history = model.fit_generator(
                    train_generator,
                    steps_per_epoch=self.number_train_samples / self.batch_size,
                    epochs=self.number_epochs,
                    validation_data=validation_generator,
                    validation_steps=self.number_validation_samples / self.batch_size,
                    callbacks=training_callbacks)

                validation_loss = history.history['val_loss']
                self.loss_history.append(validation_loss)
                self.min_losses.append(np.min(validation_loss))

            if not self.load_keras_model:
                self.i += 1

        if self.predict_test:
            self.predictions_test, test_image_names = self.directory_predict_test_augment(
                n_bags, index_number)
            return model, self.predictions_test, test_image_names
        return model

    def flow_predict_test_augment(self,
                                  X_test,
                                  model):
        """Runs Keras bagged model test data prediction with data augmentation.

        # Arguments
            X_test: (numpy array), test dataset
            model: (Keras model), trained model

        # Returns
            predictions_test: (numpy array), test data predictions
        """

        print('Predicting test set with augmentation.')
        for augment in range(self.number_test_augmentations):
            print('Augmentation number: {}'.format(augment + 1))

            if augment == 0:
                predictions_test = model.predict_generator(self.test_datagen.flow(X_test,
                                                                                  batch_size=self.batch_size),
                                                           X_test.shape[0] / self.batch_size)
            else:
                predictions_test += model.predict_generator(self.test_datagen.flow(X_test,
                                                                                   batch_size=self.batch_size),
                                                            X_test.shape[0] / self.batch_size)
            predictions_test /= self.number_test_augmentations

        print('Predictions on test data with augmentation done.',
              predictions_test.shape)
        return predictions_test

    def directory_predict_test_augment(self, n_bags, index_number=None):
        """Runs Keras bagged model test data prediction with data augmentation
            using .flow_from_directory method.

        # Arguments
            n_bags: (int), number of bags to predict on
            index_number: (Int), index specifying from which bag should training or prediction be started.

        # Returns
            predictions_test: (numpy array) test data predictions.
            test_image_names: (List), test filenames
        """

        print('Predicting set from directory: {}'.format(self.test_dir))
        predictions_test_bags = []

        if index_number is not None:
            self.i = index_number
        else:
            self.i = 1

        for bag in range(n_bags):
            print('Predicting crops for bag: {}'.format(bag + 1))
            model = self.load_trained_model('bag_dir')

            print('Model loaded.', '\n')
            for augment in range(self.number_test_augmentations):
                print('Augmentation number: {}'.format(augment + 1))

                test_generator = self.test_datagen.flow_from_directory(
                    self.test_dir,
                    target_size=self.image_size,
                    batch_size=self.batch_size,
                    seed=self.seed,
                    shuffle=False,
                    classes=None,
                    class_mode='categorical')
                test_image_names = test_generator.filenames

                if augment == 0:
                    predictions_test = model.predict_generator(test_generator,
                                                               self.number_test_samples / self.batch_size)
                else:
                    predictions_test += model.predict_generator(test_generator,
                                                                self.number_test_samples / self.batch_size)
                predictions_test /= self.number_test_augmentations
                predictions_test_bags.append(predictions_test)

            self.i += 1

        self.predictions_test = np.array(predictions_test_bags).mean(axis=0)
        print('Predictions on test data with augmentation done.')
        return self.predictions_test, test_image_names

    def perform_random_validation_split(self, split_size):
        """Performs random split into training and validation sets when
            loading data from directories.

        # Arguments
            split_size: (float), size of validation set in percents

        """

        print('Performing random split with split size: {}'.format(split_size))
        os.chdir(self.train_dir)
        os.chdir('../')
        shutil.rmtree(self.train_dir)
        shutil.rmtree(self.valid_dir)
        os.makedirs(self.train_dir, exist_ok=True)
        os.makedirs(self.valid_dir, exist_ok=True)
        copytree(self.full_train_dir, self.train_dir)
        os.chdir(self.train_dir)

        for _class in glob.glob('*'):
            os.mkdir(self.valid_dir + _class)

        train_images_names, valid_images_names = train_test_split(glob.glob(self.train_dir + '*/*.*'),
                                                                  test_size=split_size, random_state=self.seed)

        print('Number of training set images: {}, validation set images: {}'.format(len(train_images_names),
                                                                                    len(valid_images_names)))

        for i in range(len(valid_images_names)):
            os.rename(valid_images_names[i], '{}/{}'.format(self.valid_dir,
                                                            '/'.join(valid_images_names[i].split('/')[-2:])))

        return

    def output_run_statistics(self, prefix):
        """Saves statistics for each best epoch in bag/fold in current run.

        # Arguments
            - prefix: (String), specifies prefix for filename - 'bag', 'bag_dir', 'fold'
        """

        if self.verbose:
            print('Loss statistics for best epoch in current run: \n',
                  'Mean: {}'.format(np.mean(self.min_losses)), '\n',
                  'Minimum: {}'.format(np.min(self.min_losses)), '\n',
                  'Maximum: {}'.format(np.max(self.min_losses)), '\n',
                  'Standard Deviation: {}'.format(np.std(self.min_losses)), '\n')
        if self.save_statistics:
            with open('{0}{1}/{1}_{2}_{3}_run_stats_{4:.5f}.txt'.format(self.checkpoints_dst,
                                                                        self.run_save_name, prefix,
                                                                        self.i,
                                                                        np.min(self.loss_history)), 'w') as text_file:
                text_file.write(
                    '\n Loss statistics for best epoch in current run: \n')
                text_file.write('Minimum: {} \n'.format(
                    np.min(self.min_losses)))
                text_file.write('Maximum: {} \n'.format(
                    np.max(self.min_losses)))
                text_file.write('Mean: {} \n'.format(
                    np.mean(self.min_losses)))
                text_file.write('Standard Deviation: {} \n'.format(
                    np.std(self.min_losses)))
                text_file.write('Seconds it took to train the model: {} \n'.format(
                    time.time() - self.start_time))
        return

    def load_trained_model(self, prefix, importance_training=False):
        """Loads trained model based on it's checkpoint.

        # Arguments
            - prefix: (String), specifies prefix for filename

        # Returns
            - model: (Keras model), loaded trained keras model
        """

        print('Loading already trained model: {} from {} number {} \n'.format(
            self.run_save_name, prefix, self.i))

        if self.load_keras_model:
            saved_checkpoints = glob.glob('{0}{1}/*.txt'.format(self.checkpoints_dst,
                                                                self.run_save_name))
            saved_checkpoints_fold = [
                x for x in saved_checkpoints if 'fold_{}'.format(self.i) in x]
            checkpoint_fold_losses = np.array(['.'.join(
                x.split('_')[-1].split('.')[:2]) for x in saved_checkpoints_fold], dtype=np.float32)
            for i in checkpoint_fold_losses:
                self.min_losses.append(i)

        try:
            model = load_model('{0}{1}/{1}_{2}_{3}.h5'.format(self.checkpoints_dst,
                                                              self.run_save_name,
                                                              prefix,
                                                              self.i))
        except Exception:
            model = load_model('{0}{1}/{1}_{2}_{3}.h5'.format(self.checkpoints_dst,
                                                              self.run_save_name,
                                                              prefix,
                                                              self.i),
                               custom_objects={'AttentionWithContext': AttentionWithContext,
                                               'tf_roc_auc': tf_roc_auc})

        return model

    def callbacks_append_checkpoint(self, callbacks, prefix, full_training=False):
        """Appends checkpoint saving to model callbacks.

        # Arguments
            - prefix: (String), specifies prefix for filename
        """

        print('Saving model from current bag/fold: {}, {} number {} \n'.format(
            self.run_save_name, prefix, self.i))

        if full_training:
            callbacks.append(ModelCheckpoint('{0}{1}/{1}_{2}_{3}.h5'.format(
                self.checkpoints_dst,
                self.run_save_name,
                prefix,
                self.i),
                monitor='loss',
                verbose=0, save_best_only=True))
        else:
            callbacks.append(ModelCheckpoint('{0}{1}/{1}_{2}_{3}.h5'.format(
                self.checkpoints_dst,
                self.run_save_name,
                prefix,
                self.i),
                monitor='val_loss',
                verbose=0, save_best_only=True))
        return callbacks

    def callbacks_append_logger(self, callbacks, prefix):
        """Appends CSV logging to model callbacks.

        # Arguments
            - prefix: (String), specifies prefix for filename - 'bag', 'bag_dir', 'fold'
        """

        print('Saving CSV logs for model from current bag/fold: {}, {} number {} \n'.format(
            self.run_save_name, prefix, self.i))

        callbacks.append(CSVLogger('{0}{1}/{1}_{2}_{3}_history.csv'.format(
            self.checkpoints_dst,
            self.run_save_name,
            prefix,
            self.i),
            append=True))
        return callbacks

    def callbacks_append_tensorboard(self, callbacks, prefix):
        """Appends Tensorboard logging. Currently not working due to a Keras bug.

        # Arguments
            - prefix: (String), specifies prefix for filename - 'bag', 'bag_dir', 'fold'
        """

        print('Saving Tensorboard for model from current bag/fold: {}, {} number {} \n'.format(
            self.run_save_name, prefix, self.i))

        os.makedirs(
            '{0}{1}/logs/'.format(self.checkpoints_dst, self.run_save_name),
            exist_ok=True)

        callbacks.append(TensorBoard('{0}{1}/logs_{2}_{3}'.format(
            self.checkpoints_dst,
            self.run_save_name,
            prefix,
            self.i), histogram_freq=1, write_images=True))
        return callbacks


class LossLayer(Layer):
    """LossLayer outputs the loss per sample

    # Arguments
        loss: The loss function to use to combine the model output and the
              target
    """

    def __init__(self, loss, **kwargs):
        self.loss = objectives.get(loss)

        super(LossLayer, self).__init__(**kwargs)

    def build(self, input_shape):
        pass  # Nothing to do

        super(LossLayer, self).build(input_shape)

    def compute_output_shape(self, input_shape):
        # We need two inputs X and y
        assert len(input_shape) == 2

        # (None, 1) because all losses should be scalar
        return (input_shape[0][0], 1)

    def call(self, x, mask=None):
        # We need two inputs X and y
        assert len(x) == 2
        return K.expand_dims(self.loss(*x))


def tf_roc_auc(y_true, y_pred):
    return tflearn.objectives.roc_auc_score(y_pred, y_true)


class RocAucEvaluation(Callback):
    def __init__(self, validation_data=(), interval=1):
        super(Callback, self).__init__()

        self.interval = interval
        self.X_val, self.y_val = validation_data

    def on_epoch_end(self, epoch, logs={}):
        if epoch % self.interval == 0:
            y_pred = self.model.predict(self.X_val, verbose=0)
            score = roc_auc_score(self.y_val, y_pred)
            print("\n ROC-AUC - epoch: %d - score: %.6f \n" %
                  (epoch + 1, score))


class RocAucEvaluation2(Callback):
    def __init__(self, validation_data=(), evalLogFilePath=None, interval=1):
        super(Callback, self).__init__()

        self.xValidate, self.yValidate = validation_data
        self.__evalLogFilePath = evalLogFilePath
        self.interval = interval

    def on_epoch_end(self, epoch, logs={}):
        if epoch % self.interval == 0:
            yPredict = self.model.predict(self.xValidate, verbose=0)
            score = roc_auc_score(self.yValidate, yPredict)
            print("\n ROC-AUC - epoch: %d - score: %.6f \n" %
                  (epoch + 1, score))
            if self.__evalLogFilePath is not None:
                with open(self.__evalLogFilePath, 'a') as logFile:
                    logFile.write(
                        "Epoch: {:d} - val_roc_auc: {:.5f}\n".format(epoch + 1, score))
