# -- coding: utf-8 --
from __future__ import absolute_import

import re

from dateutil import parser

from .errors import Error
from .log import logger

rep_sql_regx = re.compile("('\$\|.*\|\$')")
limit_sql_regx = re.compile("(limit\s*[0-9]{1,6})")
join_on_sql_regx = re.compile("join.*on.*\s*where", re.DOTALL)


class Cursor(object):
    def __init__(self, connection):
        self.connection = connection
        self._arraysize = 1

        self.description = None
        self.rowcount = -1
        self.results = None
        self.fetched_rows = 0

    def callproc(self):
        raise ('Stored procedures not supported in Kylin')

    def close(self):
        logger.debug('Cursor close called')

    @staticmethod
    def trans_sql_for_kylin(sql_str):
        # add kylin 'join support by
        sql_str = sql_str.lower()
        rep_sql_list = rep_sql_regx.findall(sql_str)
        if not rep_sql_list:
            return sql_str
        transformed_sql = ''
        rep_sql_dict = dict()
        rep_sql_set = set()
        for rep_sql in rep_sql_list:
            rep_sql_dict[rep_sql] = rep_sql.split('|')[-2]
            rep_sql_set.add(rep_sql.split('|')[1])

        # step 1, replace all the const str to data name
        for rep_sql, rep_value in rep_sql_dict.items():
            transformed_sql = sql_str.replace(rep_sql, rep_value)

        # step 2, replace limit number from sub-sql
        limit_sql_list = limit_sql_regx.findall(transformed_sql)
        if len(limit_sql_list) == 2:
            transformed_sql = transformed_sql.replace(limit_sql_list[1], limit_sql_list[0])

        # step 3, delete 'join on' sql
        join_on_sql = join_on_sql_regx.findall(transformed_sql)
        if join_on_sql:
            transformed_sql = transformed_sql.replace(join_on_sql[0], 'where')

        # step 4, replace 'where' with rep_sql(inner join)
        for rep_sql in rep_sql_set:
            transformed_sql = transformed_sql.replace('where', '%s %s' % (rep_sql, 'where'))

        return transformed_sql

    def execute(self, operation, parameters={}, acceptPartial=True, limit=None, offset=0):

        if parameters:
            sql = operation % parameters
        else:
            sql = operation

        logger.debug('orig_sql: %s' % sql)

        pattern = re.compile(r'(\s)+count(\s)+|(\s)+count$')
        sql = pattern.sub(' __superset_count ', sql)
        # replace the conflict keywork 'count'; 解决superset与kylin关键字‘count’冲突

        pattern = re.compile(r'(\s)*\d{2}:\d{2}:\d{2}')
        sql = pattern.sub('', sql)
        # solve the issue that kylin doesn't support hh:mm:ss; 解决kylin不支持时分秒

        pattern = re.compile(r'(\s)+DEFAULT\.|(\s)+default\.')
        sql = pattern.sub(' ', sql)
        # solve for the error when the schema is 'default'; 解决schema是default时报错问题

        logger.debug('after_mod: %s' % sql)

        data = {
            # add kylin 'join support by
            'sql': self.trans_sql_for_kylin(sql.lower()),
            'offset': offset,
            'limit': limit or self.connection.limit,
            'acceptPartial': acceptPartial,
            'project': self.connection.project
        }
        logger.debug("QUERY KYLIN: %s" % data['sql'])
        resp = self.connection.proxy.post('query', json=data)

        column_metas = resp['columnMetas']

        # 还原 'count'，并将关键字改为小写，以兼容 Superset
        for c in column_metas:
            if c['label'] == 'CCOUNT':
                c['label'] = 'COUNT'
                c['name'] = 'COUNT'
            c['label'] = str(c['label']).lower()
            c['name'] = str(c['name']).lower()

        self.description = [
            [c['label'], c['columnTypeName'],
             c['displaySize'], 0,
             c['precision'], c['scale'], c['isNullable']]
            for c in column_metas
        ]

        self.results = [self._type_mapped(r) for r in resp['results']]
        self.rowcount = len(self.results)
        self.fetched_rows = 0
        return self.rowcount

    def _type_mapped(self, result):
        meta = self.description
        size = len(meta)
        for i in range(0, size):
            column = meta[i]
            tpe = column[1]
            val = result[i]
            if tpe == 'DATE':
                val = parser.parse(val)
            elif tpe == 'BIGINT':
                # trans bigint to str，not int
                val = str(val)
            elif tpe == 'INT' or tpe == 'TINYINT':
                val = int(val)
            elif tpe == 'DOUBLE' or tpe == 'FLOAT' or tpe == 'DECIMAL':
                val = float(val)
            elif tpe == 'BOOLEAN':
                val = (val == 'true')
            result[i] = val
        return result

    def executemany(self, operation, seq_params=[]):
        results = []
        for param in seq_params:
            self.execute(operation, param)
            results.extend(self.results)
        self.results = results
        self.rowcount = len(self.results)
        self.fetched_rows = 0
        return self.rowcount

    def fetchone(self):
        if self.fetched_rows < self.rowcount:
            row = self.results[self.fetched_rows]
            self.fetched_rows += 1
            return row
        else:
            return None

    def fetchmany(self, size=None):
        fetched_rows = self.fetched_rows
        size = size or self.arraysize
        self.fetched_rows = fetched_rows + size
        return self.results[fetched_rows:self.fetched_rows]

    def fetchall(self):
        fetched_rows = self.fetched_rows
        self.fetched_rows = self.rowcount
        return self.results[fetched_rows:]

    def nextset(self):
        raise Error('Nextset operation not supported in Kylin')

    @property
    def arraysize(self):
        return self._arraysize

    @arraysize.setter
    def arraysize(self, array_size):
        self._arraysize = array_size

    def setinputsizes(self):
        logger.warn('setinputsize not supported in Kylin')

    def setoutputsize(self):
        logger.warn('setoutputsize not supported in Kylin')
