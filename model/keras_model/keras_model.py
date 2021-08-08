import os
from abc import ABC, abstractmethod
from typing import Type

import numpy as np
import pandas as pd
import pytz
import utils.utils as ut
from data.data_processor import DataProcessor
from data.preprocessed_data import PreprocessedData
from data.raw_data import RawDataSource
from model.model import Model, ModelNotFoundError
from tensorflow.keras.layers import LSTM, BatchNormalization, Dense, Dropout
from tensorflow.keras.models import Sequential
from tensorflow.python.framework import errors_impl
from tensorflow.python.keras.callbacks import ModelCheckpoint

from .constants import BATCH_SIZE, SAVED_MODELS_BASE_PATH, SEQ_LEN, STEP


class KerasModel(Model, ABC):
    def __init__(self, ticker: str, preprocessed_data: Type[PreprocessedData],
                 data_processor: Type[DataProcessor], raw_data_source: Type[RawDataSource],
                 seq_len: int = SEQ_LEN, batch_size: int = BATCH_SIZE, step: int = STEP) -> None:

        super().__init__(ticker, preprocessed_data, data_processor, raw_data_source)
        self.seq_len = seq_len
        self.batch_size = batch_size
        self.step = step
        self.input_shape = (seq_len, len(self.preprocessed_data.data_processor.raw_data_source.FEATURE_KEYS))

    def train(self, epochs: int = 1):
        dataset_train, dataset_val, dataset_test = self.preprocessed_data.get_preprocessed_datasets(self.seq_len, self.batch_size, self.step)

        for batch in dataset_train.take(1):
            inputs, targets = batch
        print("Input shape:", inputs.numpy().shape)
        print("Target shape:", targets.numpy().shape)

        input_shape = (inputs.shape[1], inputs.shape[2])
        assert input_shape == self.input_shape

        model = self.__get_model()

        checkpoint_path = get_checkpoint_path(self.ticker)
        checkpoint = ModelCheckpoint(checkpoint_path, monitor='val_loss',
                                     save_best_only=True, save_weights_only=True)

        history = model.fit(
            dataset_train,
            epochs=epochs,
            validation_data=dataset_val,
            callbacks=[checkpoint]
        )

        print('History: ', history.history)
        print('Test Loss: ', model.evaluate(dataset_test))

    def __get_model(self):
        try:
            model = self.__load_saved_model()
        except ModelNotFoundError:
            model = self._create_model()
        return model

    def __load_saved_model(self):
        # the weights of the loaded model can be different from the weights of the model in train cuz only the best weights are saved.
        checkpoint_path = get_checkpoint_path(self.ticker)
        model = self._create_model()
        # latest = tf.train.latest_checkpoint(os.path.dirname(checkpoint_path))
        try:
            model.load_weights(checkpoint_path)
        except errors_impl.NotFoundError:
            raise ModelNotFoundError(self.ticker)
        return model

    def predict(self, date: str = None):
        y, pred_date = self._predict(date)
        if date is not None:
            date = ut.get_date_from_string(date)
            if pred_date != date:
                weekday_name = date.strftime('%A')
                print(f'Date given ({date}) is a {weekday_name}. So, actual prediction is for: {pred_date} (Monday)')
        return y, pred_date

    def _predict(self, date: str = None):
        df = self.preprocessed_data.data_processor.raw_data_source.get_raw_df()
        pred_date = ut.get_prediction_date(df, self.seq_len, date)
        x = self.preprocessed_data.get_preprocessed_prediction_dataset(pred_date, self.seq_len, self.batch_size, self.step)

        model = self.__load_saved_model()
        y = model.predict(x)

        scaler = self.preprocessed_data.data_processor.get_scaler()
        actual_y = self.__invTransform(scaler, y, self.preprocessed_data.data_processor.raw_data_source.CLOSE_COLUMN, self.preprocessed_data.data_processor.raw_data_source.FEATURE_KEYS)[0]

        return actual_y*100, pred_date

    @staticmethod
    def __invTransform(scaler, data, colName, colNames):
        dummy = pd.DataFrame(np.zeros((len(data), len(colNames))), columns=colNames)
        dummy[colName] = data
        dummy = pd.DataFrame(scaler.inverse_transform(dummy), columns=colNames)
        return dummy[colName].values

    @abstractmethod
    def _create_model():
        pass


# you could have various LstmModels by having their own STEP, SEQ_LEN
class LstmModel(KerasModel):

    def _create_model(self):
        model = Sequential()
        model.add(LSTM(256, input_shape=self.input_shape, return_sequences=True))
        model.add(Dropout(0.2))
        model.add(BatchNormalization())

        model.add(LSTM(128, return_sequences=True))
        model.add(Dropout(0.1))
        model.add(BatchNormalization())

        model.add(LSTM(128, return_sequences=True))
        model.add(Dropout(0.1))
        model.add(BatchNormalization())

        model.add(LSTM(128))
        model.add(Dropout(0.2))
        model.add(BatchNormalization())

        model.add(Dense(32, activation='relu'))
        model.add(Dropout(0.2))

        model.add(Dense(1))

        model.compile(
            loss='mse',
            optimizer='adam',
        )

        return model


def get_checkpoint_path(ticker):
    dirname = os.path.dirname(os.path.realpath(__file__))
    base_path = os.path.join(dirname, SAVED_MODELS_BASE_PATH)
    checkpoint_base_path = os.path.join(base_path, ticker)
    checkpoint_path = os.path.join(checkpoint_base_path, 'cp.ckpt')

    return checkpoint_path