import json
import datetime
from dateutil.parser import parse as parse_dt
import shutil
import os
import pickle

import pandas as pd

from mindsdb.interfaces.native.native import NativeInterface
from mindsdb_native import FileDS, ClickhouseDS, MariaDS, MySqlDS, PostgresDS, MSSQLDS, MongoDS, SnowflakeDS
from mindsdb.utilities.config import Config
from mindsdb.interfaces.storage.db import session, Datasource
from mindsdb.interfaces.storage.fs import FsSotre


class DataStore():
    def __init__(self, config=None):
        if config is None:
            self.config = Config()
        else:
            self.config = config

        self.fs_store = FsSotre()
        self.company_id = os.environ.get('MINDSDB_COMPANY_ID', None)
        self.dir = self.config.paths['datasources']
        self.mindsdb_native = NativeInterface()

    def get_analysis(self, name):
        datasource_record = session.query(Datasource).filter_by(company_id=self.company_id, name=name).first()
        if datasource_record.analysis is None:
            datasource_record.analysis = self.mindsdb_native.analyse_dataset(self.get_datasource_obj(name))
            session.commit()

        analysis = json.loads(datasource_record.analysis)
        return analysis

    def get_datasources(self, name):
        datasource_arr = []
        if name is not None:
            datasource_record_arr = session.query(Datasource).filter_by(company_id=self.company_id, name=name)
        else:
            datasource_record_arr = session.query(Datasource).filter_by(company_id=self.company_id)
        for datasource_record in datasource_record_arr:
            try:
                datasource = json.load(datasource_record.data)
                datasource['created_at'] = parse_dt(datasource_record.created_at.split('.')[0])
                datasource['updated_at'] = parse_dt(datasource_record.updated_at.split('.')[0])
                datasource_arr.append(datasource)
            except Exception as e:
                print(e)
        return datasource_arr

    def get_data(self, name, where=None, limit=None, offset=None):
        offset = 0 if offset is None else offset

        ds = self.get_datasource_obj(name)

        if limit is not None:
            # @TODO Add `offset` to the `filter` method of the datasource and get rid of `offset`
            filtered_ds = ds.filter(where=where, limit=limit + offset).iloc[offset:]
        else:
            filtered_ds = ds.filter(where=where)

        data = filtered_ds.to_dict(orient='records')
        return {
            'data': data,
            'rowcount': len(ds),
            'columns_names': filtered_ds.columns
        }

    def get_datasource(self, name):
        datasource_arr = self.get_datasources(name)
        if len(datasource_arr) == 1:
            return datasource_arr[0]
        # @TODO: Remove when db swithc is more stable, this should never happen, but good santiy check while this is kinda buggy
        elif len(datasource_arr) > 1:
            print('Two or more datasource with the same name, (', len(datasource_arr), ') | Full list: ', datasource_arr)
        return None

    def delete_datasource(self, name):
        session.query(Datasource).filter_by(company_id=self.company_id, name=name).delete()
        self.fs_store.delete(f'datasource_{self.company_id}_{name}')
        shutil.rmtree(os.path.join(self.dir, name))
        
    def save_datasource(self, name, source_type, source, file_path=None):
        if source_type == 'file' and (file_path is None):
            raise Exception('`file_path` argument required when source_type == "file"')

        for i in range(1, 1000):
            if name in [x['name'] for x in self.get_datasources()]:
                previous_index = i - 1
                name = name.replace(f'__{previous_index}__', '')
                name = f'{name}__{i}__'
            else:
                break

        ds_meta_dir = os.path.join(self.dir, name)
        os.mkdir(ds_meta_dir)

        try:
            if source_type == 'file':
                source = os.path.join(ds_meta_dir, source)
                shutil.move(file_path, source)
                ds = FileDS(source)

                picklable = {
                    'class': 'FileDS',
                    'args': [source],
                    'kwargs': {}
                }

            elif source_type in self.config['integrations']:
                integration = self.config['integrations'][source_type]

                ds_class_map = {
                    'clickhouse': ClickhouseDS,
                    'mariadb': MariaDS,
                    'mysql': MySqlDS,
                    'postgres': PostgresDS,
                    'mssql': MSSQLDS,
                    'mongodb': MongoDS,
                    'snowflake': SnowflakeDS
                }



                try:
                    dsClass = ds_class_map[integration['type']]
                except KeyError:
                    raise KeyError(f"Unknown DS type: {source_type}, type is {integration['type']}")

                if integration['type'] in ['clickhouse']:
                    picklable = {
                        'class': dsClass.__name__,
                        'args': [],
                        'kwargs': {
                            'query': source['query'],
                            'user': integration['user'],
                            'password': integration['password'],
                            'host': integration['host'],
                            'port': integration['port']
                        }
                    }
                    ds = dsClass(**picklable['kwargs'])

                elif integration['type'] in ['mssql', 'postgres', 'mariadb', 'mysql']:
                    picklable = {
                        'class': dsClass.__name__,
                        'args': [],
                        'kwargs': {
                            'query': source['query'],
                            'user': integration['user'],
                            'password': integration['password'],
                            'host': integration['host'],
                            'port': integration['port']
                        }
                    }

                    if 'database' in integration:
                        picklable['kwargs']['database'] = integration['database']

                    if 'database' in source:
                        picklable['kwargs']['database'] = source['database']

                    ds = dsClass(**picklable['kwargs'])

                elif integration['type'] == 'snowflake':
                    picklable = {
                        'class': dsClass.__name__,
                        'args': [],
                        'kwargs': {
                            'query': source['query'],
                            'schema': source['schema'],
                            'warehouse': source['warehouse'],
                            'database': source['database'],
                            'host': integration['host'],
                            'password': integration['password'],
                            'user': integration['user'],
                            'account': integration['account']
                        }
                    }

                    ds = dsClass(**picklable['kwargs'])

                elif integration['type'] == 'mongodb':
                    if isinstance(source['find'], str):
                        source['find'] = json.loads(source['find'])
                    picklable = {
                        'class': dsClass.__name__,
                        'args': [],
                        'kwargs': {
                            'database': source['database'],
                            'collection': source['collection'],
                            'query': source['find'],
                            'user': integration['user'],
                            'password': integration['password'],
                            'host': integration['host'],
                            'port': integration['port']
                        }
                    }

                    ds = dsClass(**picklable['kwargs'])
            else:
                # This probably only happens for urls
                ds = FileDS(source)
                picklable = {
                    'class': 'FileDS',
                    'args': [source],
                    'kwargs': {}
                }

            df = ds.df

            if '' in df.columns or len(df.columns) != len(set(df.columns)):
                shutil.rmtree(ds_meta_dir)
                raise Exception('Each column in datasource must have unique name')

            with open(os.path.join(ds_meta_dir, 'ds.pickle'), 'wb') as fp:
                pickle.dump(picklable, fp)

            with open(os.path.join(ds_meta_dir, 'metadata.json'), 'w') as fp:
                meta = {
                    'name': name,
                    'source_type': source_type,
                    'source': source,
                    'created_at': str(datetime.datetime.now()).split('.')[0],
                    'updated_at': str(datetime.datetime.now()).split('.')[0],
                    'row_count': len(df),
                    'columns': [dict(name=x) for x in list(df.keys())]
                }
                json.dump(meta, fp, indent=4, sort_keys=True)

        except Exception:
            if os.path.isdir(ds_meta_dir):
                shutil.rmtree(ds_meta_dir)
            raise

        return self.get_datasource_obj(name, raw=True), name

    def get_datasource_obj(self, name, raw=False):
        ds_meta_dir = os.path.join(self.dir, name)
        ds = None
        try:
            with open(os.path.join(ds_meta_dir, 'ds.pickle'), 'rb') as fp:
                picklable = pickle.load(fp)
                if raw:
                    return picklable
                try:
                    ds = eval(picklable['class'])(*picklable['args'], **picklable['kwargs'])
                except Exception:
                    ds = picklable
            return ds
        except Exception as e:
            print(f'\n{e}\n')
            return None
