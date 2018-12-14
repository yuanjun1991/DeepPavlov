# Copyright 2017 Neural Networks and Deep Learning lab, MIPT
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from copy import deepcopy
from itertools import product
from os.path import join
from pathlib import Path
from typing import Union, Dict, List, Generator, Iterator

from deeppavlov.core.commands.utils import expand_path
from deeppavlov.core.common.errors import ConfigError
from deeppavlov.core.common.file import read_json
from deeppavlov.core.common.log import get_logger
from deeppavlov.core.common.params_search import ParamsSearch

log = get_logger(__name__)


class PipeGen:
    """
    The :class:`~pipeline_manager.pipegen.PipeGen` implements the function of generator of standard DeepPavlov configs.
    Based on the input config, the generator creates a set of pipelines, as well as variants of the same pipeline
    with a different set of hyperparameters using the "random" or "grid" search. Also in all generated configs the save
    and load paths change to intermediate ones.
    """

    def __init__(self,
                 config: Union[str, Dict, Path],
                 save_path: Union[str, Path],
                 mode: str = 'random',
                 sample_num: int = 10,
                 test_mode: bool = False) -> None:
        """
        Initialize generator with input params.

        Args:
            config: str or dict; path to config file with search pattern, or dict with it config.
            save_path: str; path to folder with pipelines checkpoints.
            sample_num: int; determines the number of generated pipelines, if hyper_search == random.
            test_mode: bool; trigger that determine logic of changing save and loads paths in config.

        Return:
            None
        """
        if isinstance(config, dict):
            self.main_config = deepcopy(config)
        else:
            self.main_config = deepcopy(read_json(config))

        if 'chainer' not in self.main_config:
            raise ConfigError("Main config file not contain 'chainer' component."
                              "Structure search can not be started without this component.")

        self.dataset_reader = self.main_config.pop("dataset_reader")
        if not isinstance(self.main_config["dataset_iterator"], dict):
            raise ConfigError("Dataset iterator must be one for hole experiment.")

        self.train_config = self.main_config.pop("train")
        self.chainer = self.main_config.pop('chainer')
        self.structure = self.chainer['pipe']

        if mode in ['random', 'grid']:
            self.mode = mode
        else:
            raise ConfigError(f"'{mode} search' not implemented. Only 'grid' and 'random' search are available.")

        self.test_mode = test_mode
        self.save_path = save_path
        self.length = None
        self.pipes = []
        self.N = sample_num

        self._check_component_name()
        self.get_len()
        self.enumerator = self.pipeline_enumeration()
        self.generator = self.pipeline_gen()

    def _check_component_name(self) -> None:
        """
        Checks incoming config for the presence of a "component_name" key in the component description dict.

        Returns:
            None
        """
        for i, component in enumerate(self.structure):
            for j, example in enumerate(component):
                if example is not None:
                    if "component_name" not in example.keys():
                        raise ConfigError("The pipeline element in config file, on position {0} and with number {1}"
                                          "don't contain the 'component_name' key.".format(i + 1, j + 1))
        return None

    def get_len(self) -> None:
        """
        Calculate the length of generator.

        Returns:
            None
        """
        self.enumerator = self.pipeline_enumeration()
        generator = self.pipeline_gen()
        self.length = len(list(generator))
        self.pipes = []
        del generator

    def __len__(self):
        return self.length

    def pipeline_enumeration(self) -> Iterator:
        """
        Creates a primary set of pipelines using a self.main_config attribute.

        Returns:
            iterator of primary set of pipelines
        """
        if isinstance(self.dataset_reader, list):
            drs = []
            for dr in self.dataset_reader:
                drs.append(dr)
        else:
            drs = [self.dataset_reader]

        if 'batch_size' in self.train_config.keys():
            bs_conf = deepcopy(self.train_config)
            if isinstance(self.train_config['batch_size'], list):
                bss = []
                for bs in self.train_config['batch_size']:
                    bs_conf['batch_size'] = bs
                    bss.append(bs_conf)
            else:
                bss = [self.train_config]
        else:
            bss = [self.train_config]

        self.pipes.append(drs)
        self.pipes.append(bss)

        for components in self.structure:
            self.pipes.append(components)

        return product(*self.pipes)

    def pipeline_gen(self) -> Iterator:
        """
        Creates a configs with a different set of hyperparameters based on the primary set of pipelines.

        Returns:
            iterator of final sets of configs (dicts)
        """
        p = 0
        for i, variant in enumerate(self.enumerator):
            variant = list(variant)
            dr_config = variant[0]
            train_config = variant[1]
            pipe_var = variant[2:]

            if self.mode == 'random':
                for random_pipe in self.random_conf_gen(pipe_var):
                    new_config = dict(dataset_reader=deepcopy(dr_config),
                                      dataset_iterator=self.main_config['dataset_iterator'],
                                      chainer=self.chainer, train=train_config)
                    if 'metadata' in self.main_config.keys():
                        new_config['metadata'] = self.main_config['metadata']

                    chainer_components = list(random_pipe)
                    dataset_name = dr_config['data_path'].split('/')[-1]
                    chainer_components = self.change_load_path(chainer_components, p, self.save_path, dataset_name,
                                                               self.test_mode)
                    new_config['chainer']['pipe'] = chainer_components
                    p += 1
                    yield new_config
            else:
                for grid_pipe in self.grid_conf_gen(pipe_var):
                    new_config = dict(dataset_reader=deepcopy(dr_config),
                                      dataset_iterator=self.main_config['dataset_iterator'],
                                      chainer=self.chainer, train=train_config)
                    if 'metadata' in self.main_config.keys():
                        new_config['metadata'] = self.main_config['metadata']

                    chainer_components = list(grid_pipe)
                    dataset_name = dr_config['data_path'].split('/')[-1]
                    chainer_components = self.change_load_path(chainer_components, p, self.save_path, dataset_name,
                                                               self.test_mode)
                    new_config['chainer']['pipe'] = chainer_components
                    p += 1
                    yield new_config

    # random generation
    def random_conf_gen(self, pipe_components: List[dict]) -> Generator:
        """
        Creates a set of configs with a different set of hyperparameters using "random search".

        Args:
            pipe_components: list of dicts; config if components

        Returns:
            random search iterator
        """
        sample_gen = ParamsSearch(prefix="random", seed=42)
        new_pipes = []
        for component in pipe_components:
            new_components = []
            if component:
                search = False
                for key, item in component.items():
                    if isinstance(item, dict):
                        for item_key in item.keys():
                            if item_key.startswith('random_'):
                                search = True
                                break

                if search:
                    for i in range(self.N):
                        new_components.append(sample_gen.sample_params(**component))
                    new_pipes.append(new_components)
                else:
                    new_components.append(component)
                    new_pipes.append(new_components)
            else:
                pass

        for new_config in product(*new_pipes):
            yield new_config

    # grid generation
    @staticmethod
    def grid_conf_gen(pipe_components: List[dict]) -> Generator:
        """
        Creates a set of configs with a different set of hyperparameters using "grid search".

        Args:
            pipe_components: list of dicts; config if components

        Returns:
            grid search iterator
        """
        list_of_variants = []
        # find in config keys for grid search
        for i, component in enumerate(pipe_components):
            if component is not None:
                for key, item in component.items():
                    if isinstance(item, dict):
                        if 'grid_search' in item.keys():
                            var_list = list()
                            for var in item['grid_search']:
                                var_dict = dict()
                                var_dict[var] = [i, key]
                                var_list.append(var_dict)
                            list_of_variants.append(var_list)
            else:
                pass
        # create generator
        valgen = product(*list_of_variants)
        # run generator
        for variant in valgen:
            search_conf = deepcopy(pipe_components)
            for val in variant:
                for value, item in val.items():
                    search_conf[item[0]][item[1]] = value
            yield search_conf

    @staticmethod
    def change_load_path(config: List[dict],
                         n: int,
                         save_path: str,
                         dataset_name: str,
                         test_mode: bool = False) -> List[dict]:
        """
        Change save_path and load_path attributes in standard DeepPavlov config.

        Args:
            config: dict; the chainer content.
            n: int; pipeline number
            save_path: str; path to root folder where will be saved all checkpoints
            dataset_name: str; name of dataset
            test_mode: bool; trigger that determine a regime of pipeline manager work

        Returns:
            config: dict; new config with changed save and load paths
        """
        for component in config:
            if component.get('main') is True:
                if component.get('save_path', None) is not None:
                    sp = component['save_path'].split('/')[-1]
                    if not test_mode:
                        new_save_path = join(str(expand_path(save_path)), dataset_name, 'pipe_{}'.format(n + 1), sp)
                        component['save_path'] = new_save_path
                    else:
                        new_save_path = join(str(expand_path(save_path)), "tmp", dataset_name,
                                             'pipe_{}'.format(n + 1), sp)
                        component['save_path'] = new_save_path
                if component.get('load_path', None) is not None:
                    lp = component['load_path'].split('/')[-1]
                    if not test_mode:
                        new_load_path = join(str(expand_path(save_path)), dataset_name, 'pipe_{}'.format(n + 1), lp)
                        component['load_path'] = new_load_path
                    else:
                        new_load_path = join(str(expand_path(save_path)), "tmp", dataset_name,
                                             'pipe_{}'.format(n + 1), lp)
                        component['load_path'] = new_load_path
            else:
                if component.get('save_path', None) is not None:
                    sp = component['save_path'].split('/')[-1]
                    if not test_mode:
                        new_save_path = join(str(expand_path(save_path)), dataset_name, sp)
                        component['save_path'] = new_save_path
                    else:
                        new_save_path = join(str(expand_path(save_path)), "tmp", dataset_name, sp)
                        component['save_path'] = new_save_path

        return config

    def __call__(self, *args, **kwargs) -> Iterator:
        return self.generator
