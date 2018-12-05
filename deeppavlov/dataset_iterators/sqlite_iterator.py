"""
Copyright 2017 Neural Networks and Deep Learning lab, MIPT

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import sqlite3
from typing import List, Any, Dict, Optional
from random import Random
from pathlib import Path

from overrides import overrides

from deeppavlov.core.common.log import get_logger
from deeppavlov.core.common.registry import register
from deeppavlov.core.data.utils import download
from deeppavlov.core.commands.utils import expand_path
from deeppavlov.core.data.data_fitting_iterator import DataFittingIterator

logger = get_logger(__name__)


@register('sqlite_iterator')
class SQLiteDataIterator(DataFittingIterator):
    """
    Load a SQLite database, read data batches and get docs content.
    """

    def __init__(self, load_path, data_dir: str = '',
                 batch_size: int = None, shuffle: bool = None, seed: int = None,
                 db_content_type: str = 'text', **kwargs):
        """
        :param data_dir: a directory name where DB should be stored
        :param load_path: a path to local SQLite DB
        :param data_url: an URL to SQLite DB
        :param batch_size: a batch size for reading from the database
        :param text_type: can be from ['text', 'title']
        """
        self.data_dir = data_dir

        if load_path is not None:
            if load_path.startswith('http'):
                logger.info("Downloading database from url: {}".format(load_path))
                download_dir = expand_path(Path(self.data_dir))
                download_path = download_dir.joinpath(load_path.split("/")[-1])
                download(download_path, load_path, force_download=False)
            else:
                download_path = expand_path(load_path)
        else:
            raise ValueError('String path expected, got None.')

        logger.info("Connecting to database, path: {}".format(download_path))
        try:
            self.connect = sqlite3.connect(str(download_path), check_same_thread=False)
        except sqlite3.OperationalError as e:
            e.args = e.args + ("Check that DB path exists and is a valid DB file",)
            raise e
        try:
            self.db_name = self.get_db_name()
        except TypeError as e:
            e.args = e.args + (
                'Check that DB path was created correctly and is not empty. '
                'Check that a correct dataset_format is passed to the ODQAReader config',)
            raise e
        self.doc_ids = self.get_doc_ids()
        self.doc2index = self.map_doc2idx()
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.random = Random(seed)
        self.db_content_type = db_content_type

    @overrides
    def get_doc_ids(self) -> List[Any]:
        cursor = self.connect.cursor()
        cursor.execute('SELECT id FROM {}'.format(self.db_name))
        ids = [ids[0] for ids in cursor.fetchall()]
        cursor.close()
        return ids

    def get_db_name(self) -> str:
        cursor = self.connect.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        assert cursor.arraysize == 1
        name = cursor.fetchone()[0]
        cursor.close()
        return name

    def map_doc2idx(self) -> Dict[int, Any]:
        doc2idx = {doc_id: i for i, doc_id in enumerate(self.doc_ids)}
        logger.info(
            "SQLite iterator: The size of the database is {} documents".format(len(doc2idx)))
        return doc2idx

    def get_doc_titles(self) -> List[Any]:
        cursor = self.connect.cursor()
        cursor.execute('SELECT title FROM {}'.format(self.db_name))
        ids = [ids[0] for ids in cursor.fetchall()]
        cursor.close()
        return ids

    def index2title(self) -> Dict[int, Any]:
        i2t = {i: doc_id for i, doc_id in enumerate(self.get_doc_titles())}
        logger.info(
            "SQLite iterator: The size of the database is {} documents".format(len(i2t)))
        return i2t

    # def title2index(self) -> Dict[Any, int]:
    #     t2i = {v: k for k, v in self.index2title().items()}
    #     logger.info(
    #         "SQLite iterator: The size of the database is {} documents".format(len(t2i)))
    #     return t2i

    @overrides
    def get_doc_content(self, doc_id: Any) -> Optional[str]:
        """

        Args:
            doc_id:
            text_type: text_type can be from ['text', 'title']

        Returns:

        """
        cursor = self.connect.cursor()
        cursor.execute(
            "SELECT {} FROM {} WHERE id = ?".format(self.db_content_type, self.db_name),
            (doc_id,)
        )
        result = cursor.fetchone()
        cursor.close()
        return result if result is None else result[0]
