import re
from typing import Optional


class SymbolNormalizer:
    """Нормализует названия торговых пар для сравнения между биржами."""
    
    # Суффиксы, которые нужно удалять для фьючерсов и специальных контрактов
    FUTURE_SUFFIXES = [
        # Cross margin
        ':USDT', ':BUSD', ':USD', ':BTC', ':ETH', ':BNB', ':XRP',
        # Perpetual contracts
        '-PERP', '-PERPETUAL', 
        # Quarterly expiries (YYMMDD format)
        '-240621', '-240628', '-240630', '-240705', '-240712', '-240719', '-240726',
        '-240920', '-240927', '-241004', '-241011', '-241018', '-241025', '-241101', '-241108', '-241115', '-241122', '-241129', '-241206', '-241213', '-241220', '-241227',
        '-250103', '-250110', '-250117', '-250124', '-250131', '-250207', '-250214', '-250221', '-250228', '-250307', '-250314', '-250321', '-250328',
        # Bi-annual expiries
        '-240628', '-241227',
        # Weeklies
        '-240621', '-240628', '-240705', '-240712', '-240719', '-240726', '-240802', '-240809', '-240816', '-240823', '-240830', '-240906', '-240913', '-240920', '-240927',
    ]
    
    # Мappings для валют, которые разные биржи называют по-разному
    CURRENCY_MAPPINGS = {
        # Binance uses special names
        'BONK': 'BONK',
        'WIF': 'WIF',
        'PEPE': 'PEPE',
        'DOGE': 'DOGE',
        'ETH': 'ETH',
        'BTC': 'BTC',
        'USDT': 'USDT',
        'USDC': 'USDC',
        # Add more as needed
    }
    
    @classmethod
    def normalize_symbol(cls, symbol: str) -> Optional[str]:
        """
        Приводит торговый символ к каноническому виду BASE/QUOTE.
        
        Удаляет суффиксы типов контрактов (например, :USDT, -240621) 
        и возвращает название в формате "BASE/QUOTE".
        
        Args:
            symbol: Исходный символ (например, "BTC/USDT:USDT" или "BTC/USDT-240621")
            
        Returns:
            Нормализованный символ (например, "BTC/USDT") или None, если не удалось распознать
        """
        if not symbol or '/' not in symbol:
            return None
        
        # Сначала удаляем суффиксы контрактов
        normalized = cls._remove_contract_suffix(symbol)
        
        # Проверяем, что результат содержит /
        if '/' not in normalized:
            return None
        
        parts = normalized.split('/')
        if len(parts) != 2:
            return None
        
        base, quote = parts
        
        # Нормализуем названия валют (если есть маппинг)
        base_normalized = cls.CURRENCY_MAPPINGS.get(base, base)
        quote_normalized = cls.CURRENCY_MAPPINGS.get(quote, quote)
        
        return f"{base_normalized}/{quote_normalized}"
    
    @classmethod
    def _remove_contract_suffix(cls, symbol: str) -> str:
        """Удаляет суффиксы типов контрактов из символа."""
        result = symbol
        
        # Удаляем суффиксы из списка (например, :USDT, -240621)
        for suffix in cls.FUTURE_SUFFIXES:
            if result.endswith(suffix):
                result = result[:-len(suffix)]
                break
        
        # Удаляем суффиксы в формате :USDT, :BUSD (через : )
        colon_match = re.search(r':[A-Z]+$', result)
        if colon_match:
            result = result[:colon_match.start()]
        
        # Удаляем суффиксы в формате -240621 (через - и 6 цифр)
        expiry_match = re.search(r'-\d{6}$', result)
        if expiry_match:
            result = result[:expiry_match.start()]
        
        return result
    
    @classmethod
    def normalize_symbol_from_market(cls, market: dict) -> Optional[str]:
        """
        Нормализует символ из данных market в CCXT.
        
        Args:
            market: Данные рынка из exchange.markets
            
        Returns:
            Нормализованный символ или None
        """
        symbol = market.get('symbol', '')
        return cls.normalize_symbol(symbol)
    
    @classmethod
    def is_same_pair(cls, symbol1: str, symbol2: str) -> bool:
        """
        Проверяет, являются ли два символа одной и той же торговой парой.
        
        Args:
            symbol1: Первый символ
            symbol2: Второй символ
            
        Returns:
            True, если это одна и та же пара, False иначе
        """
        norm1 = cls.normalize_symbol(symbol1)
        norm2 = cls.normalize_symbol(symbol2)
        
        if norm1 is None or norm2 is None:
            return False
        
        return norm1 == norm2


def normalize_symbol(symbol: str) -> Optional[str]:
    """
    Упрощенная функция для нормализации символа.
    
    Args:
        symbol: Исходный символ
        
    Returns:
        Нормализованный символ или None
    """
    return SymbolNormalizer.normalize_symbol(symbol)