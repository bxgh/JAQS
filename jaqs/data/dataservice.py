# encoding: UTF-8

from abc import abstractmethod

import numpy as np
import pandas as pd

from jaqs.util import fileio
from jaqs.trade.pubsub import Publisher
from jaqs.data.dataapi import DataApi
from jaqs.data import align
import jaqs.util as jutil


class DataService(Publisher):
    """
    Abstract base class providing both historic and live data
    from various data sources.
    Current API version: 1.8

    Derived classes of DataServer hide different data source, but use the same API.

    Attributes
    ----------
    source_name : str
        Name of data source.

    Methods
    -------
    subscribe
    quote
    daily
    bar
    tick
    query

    """
    def __init__(self, name=""):
        Publisher.__init__(self)
        
        if name:
            self.source_name = name
        else:
            self.source_name = str(self.__class__.__name__)
    
    def init_from_config(self, props):
        pass
    
    def initialize(self):
        pass
    
    def subscribe(self, targets, callback):
        """
        Subscribe real time market data, including bar and tick,
        processed by respective callback function.

        Parameters
        ----------
        targets : str
            Security and type, eg. "000001.SH/tick,cu1709.SHF/1m"
        callback : dict of {str: callable}
            {'on_tick': func1, 'on_bar': func2}
            Call back functions.

        """
        # TODO for now it will not publish event
        for target in targets.split(','):
            sec, data_type = target.split('/')
            if data_type == 'tick':
                func = callback['on_tick']
            else:
                func = callback['on_bar']
            self.add_subscriber(func, target)
    
    @abstractmethod
    def quote(self, symbol, fields=""):
        """
        Query latest market data in DataFrame.
        
        Parameters
        ----------
        symbol : str
        fields : str, optional
            default ""

        Returns
        -------
        df : pd.DataFrame
        msg : str
            error code and error message joined by comma

        """
        pass
    
    @abstractmethod
    def daily(self, symbol, start_date, end_date, fields="", adjust_mode=None):
        """
        Query dar bar,
        support auto-fill suspended securities data,
        support auto-adjust for splits, dividends and distributions.

        Parameters
        ----------
        symbol : str
            support multiple securities, separated by comma.
        start_date : int or str
            YYYMMDD or 'YYYY-MM-DD'
        end_date : int or str
            YYYMMDD or 'YYYY-MM-DD'
        fields : str, optional
            separated by comma ',', default "" (all fields included).
        adjust_mode : str or None, optional
            None for no adjust;
            'pre' for forward adjust;
            'post' for backward adjust.

        Returns
        -------
        df : pd.DataFrame
            columns:
                symbol, code, trade_date, open, high, low, close, volume, turnover, vwap, oi, suspended
        msg : str
            error code and error message joined by comma

        Examples
        --------
        df, msg = api.daily("00001.SH,cu1709.SHF",start_date=20170503, end_date=20170708,
                            fields="open,high,low,last,volume", fq=None, skip_suspended=True)

        """
        pass
    
    @abstractmethod
    def bar(self, symbol, start_time=200000, end_time=160000, trade_date=None, freq='1m', fields=""):
        """
        Query minute bars of various type, return DataFrame.

        Parameters
        ----------
        symbol : str
            support multiple securities, separated by comma.
        start_time : int (HHMMSS) or str ('HH:MM:SS')
            Default is market open time.
        end_time : int (HHMMSS) or str ('HH:MM:SS')
            Default is market close time.
        trade_date : int (YYYMMDD) or str ('YYYY-MM-DD')
            Default is current trade_date.
        fields : str, optional
            separated by comma ',', default "" (all fields included).
        freq : trade.common.MINBAR_TYPE, optional
            {'1m', '5m', '15m'}, Minute bar type, default is '1m'

        Returns
        -------
        df : pd.DataFrame
            columns:
                symbol, code, date, time, trade_date, freq, open, high, low, close, volume, turnover, vwap, oi
        msg : str
            error code and error message joined by comma

        Examples
        --------
        df, msg = api.bar("000001.SH,cu1709.SHF", start_time="09:56:00", end_time="13:56:00",
                          trade_date="20170823", fields="open,high,low,last,volume", freq="5m")

        """
        # TODO data_server DOES NOT know "current date".
        pass
    
    @abstractmethod
    def tick(self, symbol, start_time=200000, end_time=160000, trade_date=None, fields=""):
        """
        Query tick data in DataFrame.
        
        Parameters
        ----------
        symbol : str
        start_time : int (HHMMSS) or str ('HH:MM:SS')
            Default is market open time.
        end_time : int (HHMMSS) or str ('HH:MM:SS')
            Default is market close time.
        trade_date : int (YYYMMDD) or str ('YYYY-MM-DD')
            Default is current trade_date.
        fields : str, optional
            separated by comma ',', default "" (all fields included).

        Returns
        -------
        df : pd.DataFrame
        err_msg : str
            error code and error message joined by comma
            
        """
        pass
    
    @abstractmethod
    def query(self, view, filter, fields):
        """
        Query reference data.
        Input query type and parameters, return DataFrame.
        
        Parameters
        ----------
        view : str
            Type of reference data. See doc for details.
        filter : str
            Query conditions, separated by '&'.
        fields : str
            Fields to return, separated by ','.

        Returns
        -------
        df : pd.DataFrame
        err_msg : str
            error code and error message joined by comma

        """
        pass
    
    @abstractmethod
    def get_split_dividend(self):
        pass
    
    def get_suspensions(self):
        pass


class Singleton(type):
    _instances = {}
    
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class RemoteDataService(DataService):
    """
    RemoteDataService is a concrete class using data from remote server's database.

    """
    __metaclass__ = Singleton
    # TODO no validity check for input parameters
    
    def __init__(self):
        DataService.__init__(self)

        dic = fileio.read_json(fileio.join_relative_path('etc/data_config.json'))
        address = dic.get("remote.address", None)
        username = dic.get("remote.username", None)
        password = dic.get("remote.password", None)
        if address is None or username is None or password is None:
            raise ValueError("no address, username or password available!")
        
        self.api = DataApi(address, use_jrpc=False)
        self.api.set_timeout(60)
        r, msg = self.api.login(username=username, password=password)
        if not r:
            print msg
        else:
            print "DataAPI login success.".format(address)
        
        self.REPORT_DATE_FIELD_NAME = 'report_date'
        self._calendar = None

    def __del__(self):
        self.api.close()
    
    def daily(self, symbol, start_date, end_date,
              fields="", adjust_mode=None):
        df, err_msg = self.api.daily(symbol=symbol, start_date=start_date, end_date=end_date,
                                     fields=fields, adjust_mode=adjust_mode, data_format="")
        # trade_status performance warning
        # TODO there will be duplicate entries when on stocks' IPO day
        df = df.drop_duplicates()
        return df, err_msg

    def bar(self, symbol,
            start_time=200000, end_time=160000, trade_date=None,
            freq='1m', fields=""):
        df, msg = self.api.bar(symbol=symbol, fields=fields,
                               start_time=start_time, end_time=end_time, trade_date=trade_date,
                               freq='1m', data_format="")
        return df, msg
    
    def query(self, view, filter="", fields="", **kwargs):
        """
        Get various reference data.
        
        Parameters
        ----------
        view : str
            data source.
        fields : str
            Separated by ','
        filter : str
            filter expressions.
        kwargs

        Returns
        -------
        df : pd.DataFrame
        msg : str
            error code and error message, joined by ','
        
        Examples
        --------
        res3, msg3 = ds.query("lb.secDailyIndicator", fields="price_level,high_52w_adj,low_52w_adj",\
                              filter="start_date=20170907&end_date=20170907",\
                              orderby="trade_date",\
                              data_format='pandas')
            view does not change. fileds can be any field predefined in reference data api.

        """
        df, msg = self.api.query(view, fields=fields, filter=filter, data_format="", **kwargs)
        return df, msg
    
    def get_suspensions(self):
        return None

    @property
    def calendar(self):
        if self._calendar is None:
            from jaqs.data.calendar import Calendar
            self._calendar = Calendar()
        return self._calendar
    
    def get_trade_date_range(self, start_date, end_date):
        return self.calendar.get_trade_date_range(start_date, end_date)
    
    @staticmethod
    def _dic2url(d):
        """
        Convert a dict to str like 'k1=v1&k2=v2'
        
        Parameters
        ----------
        d : dict

        Returns
        -------
        str

        """
        l = ['='.join([key, str(value)]) for key, value in d.viewitems()]
        return '&'.join(l)

    def query_lb_fin_stat(self, type_, symbol, start_date, end_date, fields="", drop_dup_cols=None):
        """
        Helper function to call data_api.query with 'lb.income' more conveniently.
        
        Parameters
        ----------
        type_ : {'income', 'balance_sheet', 'cash_flow'}
        symbol : str
            separated by ','
        start_date : int
            Annoucement date in results will be no earlier than start_date
        end_date : int
            Annoucement date in results will be no later than start_date
        fields : str, optional
            separated by ',', default ""
        drop_dup_cols : list or tuple
            Whether drop duplicate entries according to drop_dup_cols.

        Returns
        -------
        df : pd.DataFrame
            index date, columns fields
        msg : str

        """
        view_map = {'income': 'lb.income', 'cash_flow': 'lb.cashFlow', 'balance_sheet': 'lb.balanceSheet',
                    'fin_indicator': 'lb.finIndicator'}
        view_name = view_map.get(type_, None)
        if view_name is None:
            raise NotImplementedError("type_ = {:s}".format(type_))
        
        dic_argument = {'symbol': symbol,
                        'start_date': start_date,
                        'end_date': end_date,
                        # 'update_flag': '0'
                       }
        if view_name != 'lb.finIndicator':
            dic_argument.update({'report_type': '408001000'})  # we do not use single quarter single there are zeros
            """
            408001000: joint
            408002000: joint (single quarter)
            """
        
        filter_argument = self._dic2url(dic_argument)  # 0 means first time, not update
        
        res, msg = self.query(view_name, fields=fields, filter=filter_argument,
                              order_by=self.REPORT_DATE_FIELD_NAME)
        
        # change data type
        try:
            cols = list(set.intersection({'ann_date', 'report_date'}, set(res.columns)))
            dic_dtype = {col: int for col in cols}
            res = res.astype(dtype=dic_dtype)
        except:
            pass
        
        if drop_dup_cols is not None:
            res = res.sort_values(by=drop_dup_cols, axis=0)
            res = res.drop_duplicates(subset=drop_dup_cols, keep='first')
        
        return res, msg

    def query_lb_dailyindicator(self, symbol, start_date, end_date, fields=""):
        """
        Helper function to call data_api.query with 'lb.secDailyIndicator' more conveniently.
        
        Parameters
        ----------
        symbol : str
            separated by ','
        start_date : int
        end_date : int
        fields : str, optional
            separated by ',', default ""

        Returns
        -------
        df : pd.DataFrame
            index date, columns fields
        msg : str
        
        """
        filter_argument = self._dic2url({'symbol': symbol,
                                         'start_date': start_date,
                                         'end_date': end_date})
    
        return self.query("lb.secDailyIndicator",
                          fields=fields,
                          filter=filter_argument,
                          orderby="trade_date")

    def get_index_weights(self, index, trade_date):
        """
        Return all securities that have been in index during start_date and end_date.
        
        Parameters
        ----------
        index : str
            separated by ','
        trade_date : int

        Returns
        -------
        pd.DataFrame

        """
        if index == '000300.SH':
            index = '399300.SZ'
            
        filter_argument = self._dic2url({'index_code': index,
                                         'trade_date': trade_date})
    
        df_io, msg = self.query("lb.indexWeight", fields="",
                                filter=filter_argument)
        if msg != '0,':
            print msg
        df_io = df_io.set_index('symbol')
        df_io = df_io.astype({'weight': float})
        return df_io

    def get_index_weights_daily(self, index, start_date, end_date):
        """
        Return all securities that have been in index during start_date and end_date.
        
        Parameters
        ----------
        index : str
        start_date : int
        end_date : int

        Returns
        -------
        res : pd.DataFrame
            Index is trade_date, columns are symbols.

        """
        # TODO: temparary api
        trade_dates = self.get_trade_date_range(start_date, end_date)
        start_date, end_date = trade_dates[0], trade_dates[-1]
        td = start_date
        
        dic = dict()
        symbols_set = set()
        while True:
            if td > end_date:
                break
            df = self.get_index_weights(index, td)
            symbols_set.update(set(df.index))
            dic[td] = df['weight']
            td = jutil.get_next_period_day(td, 'month', 1)
        merge = pd.concat(dic, axis=1).T
        merge = merge.fillna(0.0)  # for those which are not components
        res = pd.DataFrame(index=trade_dates, columns=sorted(list(symbols_set)), data=np.nan)
        res.update(merge)
        res = res.fillna(method='ffill')
        res = res.loc[start_date: end_date]
        return res
    
    def _get_index_comp(self, index, start_date, end_date):
        """
        Return all securities that have been in index during start_date and end_date.
        
        Parameters
        ----------
        index : str
            separated by ','
        start_date : int
        end_date : int

        Returns
        -------
        list

        """
        filter_argument = self._dic2url({'index_code': index,
                                         'start_date': start_date,
                                         'end_date': end_date})
    
        df_io, msg = self.query("lb.indexCons", fields="",
                                filter=filter_argument, orderby="symbol")
        return df_io, msg
    
    def get_index_comp(self, index, start_date, end_date):
        """
        Return list of symbols that have been in index during start_date and end_date.
        
        Parameters
        ----------
        index : str
            separated by ','
        start_date : int
        end_date : int

        Returns
        -------
        list

        """
        df_io, msg = self._get_index_comp(index, start_date, end_date)
        if msg != '0,':
            print msg
        return list(np.unique(df_io.loc[:, 'symbol']))
    
    def get_index_comp_df(self, index, start_date, end_date):
        """
        Get index components on each day during start_date and end_date.
        
        Parameters
        ----------
        index : str
            separated by ','
        start_date : int
        end_date : int

        Returns
        -------
        res : pd.DataFrame
            index dates, columns all securities that have ever been components,
            values are 0 (not in) or 1 (in)

        """
        df_io, msg = self._get_index_comp(index, start_date, end_date)
        if msg != '0,':
            print msg
        
        def str2int(s):
            if isinstance(s, (str, unicode)):
                return int(s) if s else 99999999
            elif isinstance(s, (int, np.integer, float, np.float)):
                return s
            else:
                raise NotImplementedError("type s = {}".format(type(s)))
        df_io.loc[:, 'in_date'] = df_io.loc[:, 'in_date'].apply(str2int)
        df_io.loc[:, 'out_date'] = df_io.loc[:, 'out_date'].apply(str2int)
        
        # df_io.set_index('symbol', inplace=True)
        dates = self.get_trade_date_range(start_date=start_date, end_date=end_date)

        dic = dict()
        gp = df_io.groupby(by='symbol')
        for sec, df in gp:
            mask = np.zeros_like(dates, dtype=int)
            for idx, row in df.iterrows():
                bool_index = np.logical_and(dates > row['in_date'], dates < row['out_date'])
                mask[bool_index] = 1
            dic[sec] = mask
            
        res = pd.DataFrame(index=dates, data=dic)
        
        return res

    def get_industry_daily(self, symbol, start_date, end_date, type_='SW', level=1):
        """
        Get index components on each day during start_date and end_date.
        
        Parameters
        ----------
        symbol : str
            separated by ','
        start_date : int
        end_date : int
        type_ : {'SW', 'ZZ'}

        Returns
        -------
        res : pd.DataFrame
            index dates, columns symbols
            values are industry code

        """
        df_raw = self.get_industry_raw(symbol, type_=type_, level=level)
        
        dic_sec = jutil.group_df_to_dict(df_raw, by='symbol')
        dic_sec = {sec: df.sort_values(by='in_date', axis=0).reset_index()
                   for sec, df in dic_sec.viewitems()}

        df_ann_tmp = pd.concat({sec: df.loc[:, 'in_date'] for sec, df in dic_sec.viewitems()}, axis=1)
        df_value_tmp = pd.concat({sec: df.loc[:, 'industry{:d}_code'.format(level)]
                                  for sec, df in dic_sec.viewitems()},
                                 axis=1)
        
        idx = np.unique(np.concatenate([df.index.values for df in dic_sec.values()]))
        symbol_arr = np.sort(symbol.split(','))
        df_ann = pd.DataFrame(index=idx, columns=symbol_arr, data=np.nan)
        df_ann.loc[df_ann_tmp.index, df_ann_tmp.columns] = df_ann_tmp
        df_value = pd.DataFrame(index=idx, columns=symbol_arr, data=np.nan)
        df_value.loc[df_value_tmp.index, df_value_tmp.columns] = df_value_tmp

        dates_arr = self.get_trade_date_range(start_date, end_date)
        df_industry = align.align(df_value, df_ann, dates_arr)
        
        # TODO before industry classification is available, we assume they belong to their first group.
        df_industry = df_industry.fillna(method='bfill')
        df_industry = df_industry.astype(str)
        
        return df_industry
        
    def get_industry_raw(self, symbol, type_='ZZ', level=1):
        """
        Get daily industry of securities from ShenWanZhiShu or ZhongZhengZhiShu.
        
        Parameters
        ----------
        symbol : str
            separated by ','
        type_ : {'SW', 'ZZ'}
        level : {1, 2, 3, 4}
            Use which level of industry index classification.

        Returns
        -------
        df : pd.DataFrame

        """
        if type_ == 'SW':
            src = u'申万研究所'.encode('utf-8')
            if level not in [1, 2, 3, 4]:
                raise ValueError("For [SW], level must be one of {1, 2, 3, 4}")
        elif type_ == 'ZZ':
            src = u'中证指数有限公司'.encode('utf-8')
            if level not in [1, 2, 3, 4]:
                raise ValueError("For [ZZ], level must be one of {1, 2}")
        else:
            raise ValueError("type_ must be one of SW of ZZ")
        
        filter_argument = self._dic2url({'symbol': symbol,
                                         'industry_src': src})
        fields_list = ['symbol', 'industry{:d}_code'.format(level), 'industry{:d}_name'.format(level)]
    
        df_raw, msg = self.query("lb.secIndustry", fields=','.join(fields_list),
                                 filter=filter_argument, orderby="symbol")
        if msg != '0,':
            print msg
        
        df_raw = df_raw.astype(dtype={'in_date': int,
                                      # 'out_date': int
                                     })
        return df_raw.drop_duplicates()

    def get_adj_factor_daily(self, symbol, start_date, end_date, div=False):
        """
        Get index components on each day during start_date and end_date.
        
        Parameters
        ----------
        symbol : str
            separated by ','
        start_date : int
        end_date : int
        div : bool
            False for normal adjust factor, True for diff.

        Returns
        -------
        res : pd.DataFrame
            index dates, columns symbols
            values are industry code

        """
        df_raw = self.get_adj_factor_raw(symbol, start_date=start_date, end_date=end_date)
    
        dic_sec = jutil.group_df_to_dict(df_raw, by='symbol')
        dic_sec = {sec: df.set_index('trade_date').loc[:, 'adjust_factor']
                   for sec, df in dic_sec.viewitems()}
        
        # TODO: duplicate codes with dataview.py: line 512
        res = pd.concat(dic_sec, axis=1)  # TODO: fillna ?
        
        idx = np.unique(np.concatenate([df.index.values for df in dic_sec.values()]))
        symbol_arr = np.sort(symbol.split(','))
        res_final = pd.DataFrame(index=idx, columns=symbol_arr, data=np.nan)
        res_final.loc[res.index, res.columns] = res

        # align to every trade date
        s, e = df_raw.loc[:, 'trade_date'].min(), df_raw.loc[:, 'trade_date'].max()
        dates_arr = self.get_trade_date_range(s, e)
        if not len(dates_arr) == len(res_final.index):
            res_final = res_final.reindex(dates_arr)
            
            res_final = res_final.fillna(method='ffill').fillna(method='bfill')

        if div:
            res_final = res_final.div(res_final.shift(1, axis=0)).fillna(1.0)
            
        # res = res.loc[start_date: end_date, :]

        return res_final

    def get_adj_factor_raw(self, symbol, start_date=None, end_date=None):
        """
        Query adjust factor for symbols.
        
        Parameters
        ----------
        symbol : str
            separated by ','
        start_date : int
        end_date : int

        Returns
        -------
        df : pd.DataFrame

        """
        if start_date is None:
            start_date = ""
        if end_date is None:
            end_date = ""
        
        filter_argument = self._dic2url({'symbol': symbol,
                                         'start_date': start_date, 'end_date': end_date})
        fields_list = ['symbol', 'trade_date', 'adjust_factor']

        df_raw, msg = self.query("lb.secAdjFactor", fields=','.join(fields_list),
                                 filter=filter_argument, orderby="symbol")
        if msg != '0,':
            print msg
        df_raw = df_raw.astype(dtype={'symbol': str,
                                    'trade_date': int,
                                    'adjust_factor': float
                                    })
        return df_raw.drop_duplicates()
    
    def query_inst_info(self, symbol, inst_type="", fields=""):
        if inst_type == "":
            inst_type = "1,2,3,4,5,101,102,103,104"
        
        filter_argument = self._dic2url({'symbol': symbol,
                                         'inst_type': inst_type})
    
        df_raw, msg = self.query("jz.instrumentInfo", fields=fields,
                                 filter=filter_argument, orderby="symbol")
        if msg != '0,':
            print msg

        dtype_map = {'symbol': str, 'list_date': int, 'delist_date': int, 'inst_type': int}
        cols = set(df_raw.columns)
        dtype_map = {k: v for k, v in dtype_map.viewitems() if k in cols}
        
        df_raw = df_raw.astype(dtype=dtype_map)
        
        res = df_raw.set_index('symbol')
        return res
