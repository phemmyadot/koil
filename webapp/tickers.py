"""
Screened ticker universe for the exhaustion dashboard.

Rebuild by running scratchpad build_universe.py (screens Yahoo's equity screener
for cap 300M+, avg vol >500K, price $5-$50, then filters each candidate for
price>SMA200, SMA50>SMA200, and weekly volatility >5% -- matches the criteria
used to curate p.py's target_universe.
"""

TICKERS = [
    'AAL', 'ABCL', 'ABEO', 'ABSI', 'ABX', 'ACDC', 'ACHC', 'ACHV',
    'ACRS', 'ADEA', 'ADPT', 'ADTN', 'AESI', 'AEVA', 'AGIO', 'AGRO',
    'AIP', 'ALM', 'ALMS', 'ALOY', 'ALTO', 'AMCX', 'AMLX', 'AMN',
    'AMRX', 'ANNX', 'AOSL', 'APA', 'APPS', 'ARKO', 'ARTV', 'ASC',
    'ASPN', 'ASTH', 'ASX', 'ATAI', 'AUR', 'AURA', 'AVBP', 'AVNS',
    'AVNT', 'AVR', 'AVTX', 'AXGN', 'BAX', 'BAYRY', 'BB', 'BBAR',
    'BCAX', 'BCRX', 'BEAM', 'BFLY', 'BHVN', 'BIOA', 'BIRK', 'BKD',
    'BLFS', 'BLMN', 'BLZE', 'BRUN', 'BVS', 'BW', 'BZH', 'CADL',
    'CALY', 'CART', 'CC', 'CCRN', 'CDNA', 'CEVA', 'CGAU', 'CGEM',
    'CGNT', 'CIFR', 'CLDX', 'CLMT', 'CLSK', 'CLYM', 'CMBT', 'CMP',
    'CMPS', 'CNK', 'CODI', 'COGT', 'COLD', 'CORZ', 'CRI', 'CRNC',
    'CRSR', 'CRVS', 'CSTM', 'CTOS', 'CVI', 'CYRX', 'CZR', 'DBRG',
    'DFTX', 'DMRA', 'DNLI', 'DOW', 'DRS', 'DRTS', 'DSGN', 'DYN',
    'EFXT', 'EGY', 'ELAN', 'ENPH', 'EOLS', 'EPC', 'EQNR', 'ERAS',
    'ESI', 'ETOR', 'EVC', 'EVH', 'EWTX', 'EXTR', 'F', 'FA',
    'FANUY', 'FCEL', 'FDMT', 'FIVN', 'FLYW', 'FNKO', 'FRCOY', 'FRO',
    'FSLY', 'FTRE', 'FUN', 'GBTG', 'GEN', 'GEO', 'GLUE', 'GPRE',
    'GPRK', 'GRND', 'GRPN', 'GRRR', 'GTBIF', 'GTES', 'GTX', 'HAL',
    'HAPN', 'HBM', 'HCSG', 'HELE', 'HGV', 'HIMX', 'HLIT', 'HLX',
    'HP', 'HPE', 'HPK', 'HPP', 'HPQ', 'HRMY', 'HTLD', 'HUBG',
    'HUN', 'IART', 'IBRX', 'ILPT', 'IMAX', 'IMMX', 'IMNM', 'IMVT',
    'INDV', 'IRDM', 'JBIO', 'JBLU', 'JHX', 'KMT', 'KOD', 'KURA',
    'KYIV', 'LAR', 'LBRT', 'LEGN', 'LEVI', 'LFST', 'LILA', 'LILAK',
    'LIND', 'LION', 'LPG', 'LPTH', 'LSEGY', 'LTH', 'LUV', 'LXU',
    'LZB', 'M', 'MAMA', 'MAN', 'MD', 'MEI', 'MGNI', 'MGTX',
    'MITK', 'MLTX', 'MRAAY', 'MRAM', 'MRVI', 'MUR', 'NAT', 'NE',
    'NEO', 'NEOG', 'NESR', 'NEXA', 'NEXT', 'NOK', 'NOKBF', 'NOV',
    'NRIX', 'NSA', 'NSP', 'NVCR', 'NVRI', 'NVST', 'NVTS', 'NWL',
    'OBE', 'OGN', 'OMCL', 'OMDA', 'OPLN', 'OPTX', 'OSCR', 'OSS',
    'OUST', 'PACK', 'PACS', 'PANL', 'PAYO', 'PAYS', 'PBI', 'PCRX',
    'PENN', 'PGEN', 'PGNY', 'PL', 'POET', 'PRAA', 'PRCH', 'PRG',
    'PRKS', 'PRM', 'PRMB', 'PSNL', 'PTEN', 'PUBM', 'PUMP', 'QMCO',
    'QTTB', 'QURE', 'RAMP', 'RCRUY', 'RCUS', 'RELY', 'REPL', 'RHI',
    'RIG', 'RIOT', 'RLAY', 'RLMD', 'RNECY', 'RNG', 'RSI', 'RXO',
    'RYCEY', 'S', 'SFTBY', 'SG', 'SGHC', 'SGMT', 'SHLS', 'SHOO',
    'SKE', 'SKM', 'SKYT', 'SLB', 'SLDB', 'SLDE', 'SLG', 'SLS',
    'SM', 'SMERY', 'SMTOY', 'SNDR', 'SNDX', 'SPIR', 'SRTA', 'SSL',
    'SSRM', 'STAA', 'STGW', 'STTK', 'SVCO', 'SVRA', 'SW', 'SWBI',
    'SXC', 'TALK', 'TALO', 'TBLA', 'TDAY', 'TDC', 'TDOC', 'TE',
    'TENB', 'TENX', 'TEO', 'TEVA', 'TGB', 'TH', 'TILE', 'TK',
    'TNGX', 'TOI', 'TREX', 'TRLV', 'TRMD', 'TRN', 'TRVI', 'TSHA',
    'TTI', 'TWO', 'TXG', 'TYRA', 'UA', 'UAA', 'ULCC', 'UMAC',
    'UMC', 'UNFI', 'UNIT', 'URGN', 'UTI', 'VCEL', 'VELO', 'VG',
    'VIAV', 'VIR', 'VRNS', 'VSH', 'VSTS', 'WBD', 'WERN', 'WEST',
    'WNC', 'WRBY', 'WSC', 'WT', 'WTTR', 'WULF', 'WYFI', 'XMAX',
    'XPRO', 'YETI', 'YPF', 'ZETA', 'ZGN', 'ZIM', 'ZVRA', 'ZYME',
]
