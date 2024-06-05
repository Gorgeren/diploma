import psycopg2
import yaml
import hashlib
import random
import murmurhash
import re
import time
import os
from threading import Thread
import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal
from pglast import parse_sql
from contextlib import AbstractContextManager
from typing import Optional, Tuple, Union
from pglast import ParseError, parse_sql
from pglast.nodes import InsertStmt, SelectStmt, UpdateStmt

def _hash(key: str):
    return murmurhash.hash(key) & 0xFFFFFFFF

class pgShard:
    def __init__(self, *, configfilepath: str, autocommit=False, tablesinfo='tables.yaml', pointsfile=None):
        self.db_list = []
        self.tables = []
        self.transfering = False
        self.inDataBase = 0
        self.total = 0
        self.autocommit = autocommit
        self.points = []
        self.db_unique_key = []
        self.configfilepath = configfilepath
        self.config_hash = None
        self.system_db = None

        with open(tablesinfo, 'r') as tables:
            tableconfig = yaml.safe_load(tables)
        self.tables = tableconfig.get('tables')
        
        if configfilepath:
            self.config_hash = self.calculate_file_hash(configfilepath)
            with open(configfilepath, 'r') as file:
                configs = yaml.safe_load(file)
            configs_db = configs.get('databases', [])
            configs_db = self.filter_db_configs(configs_db)
            confsys = configs.get('system_db')

            self.system_db = psycopg2.connect(**confsys)
            self.system_db.autocommit = True
            self.connect_base(configs_db)
            self.inDataBase = len(configs_db)
        else:
            raise ValueError("Config file path must be provided")

        self.create_system_db()
        self.create_tables()
        self.initialize_points(pointsfile)
        self.start_config_check_thread()

    class Cursor:
        def __init__(self, shard):
            self.shard = shard
            self.query = None
            self.results = None
            self.current_row = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def execute(self, query: str):
            self.query = query
            self.results = self.shard.execute(query)
            self.current_row = 0

        def fetchall(self):
            if self.results is None:
                raise Exception("No query executed yet")
            return self.results

        def fetchone(self):
            if self.results is None:
                raise Exception("No query executed yet")
            if self.current_row < len(self.results):
                row = self.results[self.current_row]
                self.current_row += 1
                return row
            else:
                return None
            
    def initialize_points(self, pointsfile):
        if pointsfile is None:
            pointsfile = 'points.yaml'
        if not os.path.exists(pointsfile):
            open(pointsfile, 'a').close()
        
        with self.system_db.cursor() as cursor:
            cursor.execute("SELECT COUNT(1) FROM ShardPoints")
            points_count = cursor.fetchone()[0]

        if points_count == 0:
            if os.path.getsize(pointsfile) == 0:
                self.gen_points(pointsfile)
            self.load_points_from_file(pointsfile)
        else:
            self.load_points_from_db()
        if os.path.getsize(pointsfile) == 0 and points_count > 0:
            self.dump_points_to_file(pointsfile)

    def dump_points_to_file(self, pointsfile):
        with self.system_db.cursor() as cursor:
            cursor.execute("SELECT db_connection_str, points FROM ShardPoints")
            data = {db_str: points for db_str, points in cursor}
        
        with open(pointsfile, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
    def gen_points(self, pointsfile: str):
        with open(self.configfilepath, 'r') as file:
            configs = yaml.safe_load(file)
        configs_db = configs.get('databases', [])
        
        points_data = {}
        
        for db_config in configs_db:
            vnodes = db_config.get('VNodes', 1)
            db_str = self.db_config_to_str(db_config)
            
            points_data[db_str] = []
            for _ in range(vnodes):
                points_data[db_str].append(random.randint(0, 2**32 - 1))
        
        with open(pointsfile, 'w') as f:
            yaml.dump(points_data, f, default_flow_style=False)
        
        self.load_points(pointsfile)
        self.update_shard_points_db(points_data)

    def load_points_from_db(self):
        with self.system_db.cursor() as cursor:
            cursor.execute("SELECT db_connection_str, points FROM ShardPoints")
            points_data = cursor.fetchall()

        if points_data:
            data = {db_str: points for db_str, points in points_data}
            self.points = [(point, db) for db, point_list in data.items() for point in point_list]
            self.points.sort(key=lambda x: x[0])
            self.update_shard_points_db(data)

    def load_points_from_file(self, pointsfile):
        with open(pointsfile, 'r') as f:
            data = yaml.safe_load(f)
            self.points = [(point, db) for db, point_list in data.items() for point in point_list]
        self.points.sort(key=lambda x: x[0])
        self.update_shard_points_db(data)

    def update_shard_points_db(self, points_data):
        with self.system_db.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE ShardPoints")
            for db_str, points in points_data.items():
                cursor.execute(
                    "INSERT INTO ShardPoints (db_connection_str, points) VALUES (%s, %s)",
                    (db_str, json.dumps(points))
                )

    def create_system_db(self):
        shardname = 'ShardStatus'
        shardStatus = f"""CREATE TABLE IF NOT EXISTS {shardname} (IsShardingActive BIT NOT NULL);"""
        pointStatus = f"""CREATE TABLE IF NOT EXISTS pointStatus (ArePointsUpdated BIT NOT NULL);"""
        shardPoints = f"""CREATE TABLE IF NOT EXISTS ShardPoints (
                            db_connection_str TEXT PRIMARY KEY,
                            points JSONB
                        );"""
        with self.system_db.cursor() as cursor:
            for table_name in self.tables:
                cursor.execute(f"""CREATE TABLE IF NOT EXISTS {table_name+'system'} (blockedRow VARCHAR(255) PRIMARY KEY);""")
                    
            cursor.execute(shardStatus)
            cursor.execute(f"""INSERT INTO {shardname} (IsShardingActive) 
                            SELECT B'0'
                            WHERE NOT EXISTS (SELECT 1 FROM {shardname});""")
            
            cursor.execute(pointStatus)
            cursor.execute(f"""INSERT INTO pointStatus (ArePointsUpdated) 
                            SELECT B'0'
                            WHERE NOT EXISTS (SELECT 1 FROM pointStatus);""")
            
            cursor.execute(shardPoints)

    def filter_db_configs(self, configs_db):
        allowed_keys = {'host', 'database', 'user', 'password', 'port'}
        return [{key: value for key, value in db_config.items() if key in allowed_keys} for db_config in configs_db]
    
    def calculate_file_hash(self, filepath: str) -> str:
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()

    def check_for_config_changes(self):
        new_hash = self.calculate_file_hash(self.configfilepath)
        if new_hash != self.config_hash:
            print("Config file has changed. Updating shards...", file=sys.stderr)
            self.config_hash = new_hash
            with open(self.configfilepath, 'r') as file:
                configs = yaml.safe_load(file)
            configs_db = configs.get('databases', [])
            self.update_shards(configs_db)

    def update_shards(self, new_shards):
        current_shards = set(self.db_unique_key)
        new_shard_configs = {self.db_config_to_str(config) for config in new_shards}
        shards_to_add = new_shard_configs - current_shards
        shards_to_remove = current_shards - new_shard_configs

        for shard_str in shards_to_remove:
            self.remove_shard(shard_str)

        for shard_config in new_shards:
            if self.db_config_to_str(shard_config) in shards_to_add:
                self.add_shard(shard_config)

    def get_connection_string(self, conn):
        user = conn.get_dsn_parameters()['user']
        database = conn.get_dsn_parameters()['dbname']
        host = conn.get_dsn_parameters()['host']
        port = conn.get_dsn_parameters()['port']
        return f"{user}:{database}:{host}:{port}"

    def db_config_to_str(self, config):
        return f"{config['user']}:{config['database']}:{config['host']}:{config['port']}"

    def add_shard(self, db_config):
        try:
            with self.system_db.cursor() as cursor:
                # Check if sharding is active
                cursor.execute("SELECT IsShardingActive FROM ShardStatus")
                is_sharding_active = cursor.fetchone()[0]

                if is_sharding_active:
                    raise Exception("Sharding is already active. Cannot add new shard at this time.")

                # Set sharding to active
                cursor.execute("UPDATE ShardStatus SET IsShardingActive = B'1'")

            conn = psycopg2.connect(**db_config)
            if self.autocommit:
                conn.autocommit = True

            db_str = self.db_config_to_str(db_config)
            self.db_list.append(conn)
            self.db_unique_key.append(db_str)
            self.total += 1
            print(f"Added shard: {db_str}")

            new_point = random.randint(0, 2**32 - 1)
            self.points.append((new_point, db_str))
            self.points.sort(key=lambda x: x[0])
            
            self.update_shard_points_db({db_str: [new_point]})

            points_data = {db_str: [new_point] for _, db_str in self.points}
            with open('points.yaml', 'w') as f:
                yaml.dump(points_data, f, default_flow_style=False)

            rebalance_thread = Thread(target=self.rebalance_shards, args=(db_str,))
            rebalance_thread.start()
            
        except Exception as e:
            print(f"Failed to add shard: {db_config}. Error: {e}")
            with self.system_db.cursor() as cursor:
                # Reset sharding status to inactive
                cursor.execute("UPDATE ShardStatus SET IsShardingActive = B'0'")
            raise

    def rebalance_shards(self, new_db_str):
        try:
            batch_size = 1000

            for _, db_str in self.points:
                if db_str == new_db_str:
                    continue

                conn = self.db_list[self.db_unique_key.index(db_str)]
                table_info = self.TableInfo({'table_name': self.tables['table_name'], 'columns': self.tables['columns']})

                offset = 0
                while True:
                    with conn.cursor() as cursor:
                        cursor.execute(f"SELECT * FROM {self.tables['table_name']} LIMIT {batch_size} OFFSET {offset}")
                        records = cursor.fetchall()
                        if not records:
                            break

                        shared_keys = [record[self.tables['table_name']['shard_key']] for record in records]
                        insert_queries = [table_info.generate_insert_query([record]) for record in records]

                        for i, shared_key in enumerate(shared_keys):
                            hashed_key = _hash(shared_key)
                            target_db = self.find_next(hashed_key)
                            if target_db != db_str:
                                with self.db_list[self.db_unique_key.index(target_db)].cursor() as target_cursor:
                                    target_cursor.execute(insert_queries[i])
                                with conn.cursor() as del_cursor:
                                    del_cursor.execute(f"DELETE FROM {self.tables['table_name']} WHERE {self.tables['table_name']['shard_key']} = %s", (shared_key,))
                                with self.system_db.cursor() as system_cursor:
                                    system_cursor.execute(f"DELETE FROM {self.tables['table_name']}system WHERE blockedRow = %s", (shared_key,))

                        offset += batch_size

            with self.system_db.cursor() as cursor:
                cursor.execute("UPDATE ShardStatus SET IsShardingActive = B'0'")

        except Exception as e:
            print(f"Failed to rebalance shards. Error: {e}")
            with self.system_db.cursor() as cursor:
                cursor.execute("UPDATE ShardStatus SET IsShardingActive = B'0'")
            raise

    def remove_shard(self, shard_str):
        for i, db in enumerate(self.db_list):
            print(i, db)
            if self.get_connection_string(db) == shard_str:
                print("deleted:",shard_str)
                db.close()
                del self.db_list[i]
                del self.db_unique_key[i]
                self.total -= 1
                print(f"Removed shard: {shard_str}", file=sys.stderr)
                break


    def create_tables(self):
        for db in self.db_list:
            with db.cursor() as cursor:
                for table_name in self.tables:
                    cursor.execute(f"""SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = '{table_name}');""")
                    exist = cursor.fetchone()
                    if not exist[0]:
                        cursor.execute(self.tables[table_name]['sql'])

    def connect_base(self, shards_list):
        for db_config in shards_list:
            try:
                self.db_list.append(psycopg2.connect(**db_config))
                if(self.autocommit):
                    self.db_list[-1].autocommit = True
                self.total += 1
                self.db_unique_key.append(self.db_config_to_str(db_config))
            except Exception as e:
                print(f"Failed to connect to {db_config}: {e}")
                self.close_connections()
                raise
        print("All connections are OK")

    def close_connections(self):
        for connection in self.db_list:
            connection.close()
        self.db_list = []

    def start_config_check_thread(self):
        check_interval = 5  
        self.config_check_thread = Thread(target=self.check_config_periodically, args=(check_interval,))
        self.config_check_thread.daemon = True
        self.config_check_thread.start()

    def check_config_periodically(self, interval):
        while True:
            self.check_for_config_changes()
            time.sleep(interval)

    def __del__(self):
        self.close_connections()
        if hasattr(self, 'config_check_thread') and self.config_check_thread.is_alive():
            self.config_check_thread.join()

    def execute(self, query: str):
            combined_results = []
            with ThreadPoolExecutor(max_workers=len(self.db_list)) as executor:
                futures = [executor.submit(self.execute_worker, query, i) for i in range(len(self.db_list))]
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        combined_results.extend(result.fetchall())
            return combined_results if combined_results else None
    
    def insert(self, query: str, shard_id: int):
        parsed_query = parse_sql(query)
        
        if len(parsed_query) != 1 or not isinstance(parsed_query[0], InsertStmt):
            raise ValueError("Query is not a valid INSERT statement")

        insert_stmt = parsed_query[0]
        table_name = insert_stmt.relation.relname
        columns = ', '.join(col.name for col in insert_stmt.cols[0].fields)
        values_lists = insert_stmt.select_stmt.values_lists
        
        if len(values_lists) != 1:
            raise ValueError("Multiple rows insertion is not supported")

        values = values_lists[0]
        sharding_column = self.tables[table_name]['shard_key']
        column_idx = [i for i, col in enumerate(insert_stmt.cols[0].fields) if col.name == sharding_column][0]

        insert_values = [val.str for val in values[column_idx]]
        hashed_values = [_hash(val) for val in insert_values]

        if self.db_unique_key[shard_id] == self.find_next(hashed_values):
            placeholders = ', '.join(['%s'] * len(insert_values))
            system_table_name = f"{table_name}system"
            check_query = f"SELECT blockedRow FROM {system_table_name} WHERE blockedRow IN ({placeholders})"
            with self.db_list[shard_id].cursor() as cursor:
                cursor.execute(check_query, tuple(insert_values))
                existing_keys = cursor.fetchall()

            if existing_keys:
                existing_keys_set = {key[0] for key in existing_keys}
                raise Exception(f"Attempt to access blocked rows: {existing_keys_set}")

            final_values = ', '.join(f"({', '.join(val.str for val in row)})" for row in values)
            if final_values:
                resquery = f"INSERT INTO {table_name} ({columns}) VALUES {final_values};"
                with self.db_list[shard_id].cursor() as cursor:
                    cursor.execute(resquery)

        
    def handle_select(self, query, shard_id, matches):
        table_name = matches[0][1]
        where_clause = matches[0][3]
        sharding_column = self.tables[table_name]['shard_key']
        shard_key_values = []

        if where_clause:
            regwhere = re.compile(rf'\b{sharding_column}\s*=\s*[\'"]?(\w+)[\'"]?')
            shard_key_values = re.findall(regwhere, where_clause)

        if shard_key_values:
            is_sharding_active = self.is_sharding_active()

            if self.is_key_locked(shard_key_values):
                raise Exception(f"One or more shard keys are locked: {shard_key_values}")

            results = []
            for shard_key_value in shard_key_values:
                hashed_value = _hash(shard_key_value)
                target_db = self.find_next(hashed_value)
                current_db = self.get_connection_string(self.db_list[shard_id])
                previous_db = self.get_previous_db(target_db)

                if is_sharding_active:
                    for db in [target_db, previous_db]:
                        if db == current_db:
                            cursor = self.db_list[shard_id].cursor()
                            cursor.execute(query)
                            results.extend(cursor.fetchall())
                else:
                    if target_db == current_db:
                        cursor = self.db_list[shard_id].cursor()
                        cursor.execute(query)
                        results.extend(cursor.fetchall())

            return results
        else:
            cursor = self.db_list[shard_id].cursor()
            cursor.execute(query)
            return cursor.fetchall()

    def get_previous_db(self, current_db):
        current_index = self.db_unique_key.index(current_db)
        previous_index = (current_index - 1) % len(self.db_unique_key)
        return self.db_list[previous_index]

    def is_sharding_active(self):
        with self.system_db.cursor() as cursor:
            cursor.execute("SELECT IsShardingActive FROM ShardStatus")
            return cursor.fetchone()[0] == '1'

    def is_key_locked(self, shard_key_values):
        shard_key_values_tuple = tuple(shard_key_values)
        
        with self.system_db.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(1) FROM {self.system_table} WHERE blockedRow IN %s",
                (shard_key_values_tuple,)
            )
            return cursor.fetchone()[0] > 0
        
    def handle_update(self, query: str, shard_id: int):
        parsed_query = parse_sql(query)
        
        if len(parsed_query) != 1 or not isinstance(parsed_query[0], UpdateStmt):
            raise ValueError("Query is not a valid UPDATE statement")

        update_stmt = parsed_query[0]
        table_name = update_stmt.relation.relname
        set_clause = update_stmt.targetList
        where_clause = update_stmt.whereClause
        
        if where_clause:
            sharding_column = self.tables[table_name]['shard_key']
            regwhere = re.compile(rf'\b{sharding_column}\s*=\s*[\'"]?(\w+)[\'"]?')
            shard_key_values = re.findall(regwhere, where_clause)

            if shard_key_values:
                is_sharding_active = self.is_sharding_active()

                if self.is_key_locked(shard_key_values):
                    raise Exception(f"One or more shard keys are locked: {shard_key_values}")

                for shard_key_value in shard_key_values:
                    hashed_value = _hash(shard_key_value)
                    target_db = self.find_next(hashed_value)
                    current_db = self.get_connection_string(self.db_list[shard_id])
                    previous_db = self.get_previous_db(target_db)

                    if is_sharding_active:
                        for db in [target_db, previous_db]:
                            if db == current_db:
                                with self.db_list[shard_id].cursor() as cursor:
                                    cursor.execute(query)
                    else:
                        if target_db == current_db:
                            with self.db_list[shard_id].cursor() as cursor:
                                cursor.execute(query)
            else:
                with self.db_list[shard_id].cursor() as cursor:
                    cursor.execute(query)
        else:
            with self.db_list[shard_id].cursor() as cursor:
                cursor.execute(query)
        return

    def execute_worker(self, query: str, shard_id: int):
        parsed_query = parse_sql(query)
        
        if len(parsed_query) != 1 or not isinstance(parsed_query[0], (InsertStmt, SelectStmt, UpdateStmt)):
            raise ValueError("Unsupported query type")

        if isinstance(parsed_query[0], InsertStmt):
            return self.insert(query, shard_id)

        elif isinstance(parsed_query[0], SelectStmt):
            return self.handle_select(query, shard_id)

        elif isinstance(parsed_query[0], UpdateStmt):
            return self.handle_update(query, shard_id)

    class TableInfo:
        def __init__(self, info):
            self.table_name = info['table_name']
            self.columns = info['columns']

        def generate_insert_query(self, records):
            values_parts = []
            for record in records:
                formatted_record = ', '.join(self._format_value(value) for value in record)
                values_parts.append(f"({formatted_record})")
            
            values_part = ', '.join(values_parts)
            
            insert_into_part = f"INSERT INTO {self.table_name} ({', '.join(self.columns.keys())}) VALUES {values_part}"
            return insert_into_part

        def _format_value(self, value):
            if isinstance(value, str):
                return value
            elif isinstance(value, datetime):
                return f"'{value.isoformat()}'"
            elif isinstance(value, Decimal):
                return str(value)
            elif isinstance(value, (int, float)):
                return str(value)
            elif value is None:
                return 'NULL'
            else:
                return str(value).lower()

    def cursor(self):
        return self.Cursor(self)



if __name__ == "__main__":
    pg = pgShard(configfilepath='config.yaml', autocommit=True, pointsfile='points.yaml')
    
    with pg.cursor() as cursor:
        cursor.execute("""SELECT COUNT(*) FROM users;""")
        result = cursor.fetchone()
        print(result)