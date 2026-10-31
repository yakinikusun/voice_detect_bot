import unicodedata

# カタカナ (ァ〜ヶ) をひらがな (ぁ〜ゖ) に変換するテーブル (0x60 差)
_KATA_TO_HIRA = str.maketrans({c: chr(c - 0x60) for c in range(ord("ァ"), ord("ヶ") + 1)})


def normalize(s: str) -> str:
    """表記ゆれを減らした比較用文字列を返す。

    処理: NFKC正規化 -> 小文字化 -> カタカナをひらがなに -> 空白と句読点を除去
    """
    # 1. NFKC正規化
    s = unicodedata.normalize("NFKC", s)
    # 2. 小文字化
    s = s.lower()
    # 3. カタカナをひらがなに
    s = s.translate(_KATA_TO_HIRA)
    # 4. 空白と句読点を除去 (Unicodeのカテゴリ P: 句読点, Z: 空白区切り文字, または \\s)
    chars = []
    for c in s:
        if c.isspace():
            continue
        cat = unicodedata.category(c)
        if cat.startswith("P"):  # Punctuation (、。,.!?など)
            continue
        chars.append(c)
    return "".join(chars)


class KeywordDetector:
    """キーワードを正規化して保持し、テキスト内の検知を行う"""

    def __init__(self, keywords: list[str]):
        self._targets: list[tuple[str, str]] = []  # (original, normalized)
        seen_originals = set()
        for kw in keywords:
            cleaned = kw.strip()
            if not cleaned or cleaned in seen_originals:
                continue
            norm = normalize(cleaned)
            if not norm:
                continue
            seen_originals.add(cleaned)
            self._targets.append((cleaned, norm))

    def detect(self, text: str) -> list[str]:
        """text に含まれるキーワードを元の表記の設定順(重複なし)で返す"""
        norm_text = normalize(text)
        if not norm_text:
            return []

        matched = []
        for orig, norm in self._targets:
            if norm in norm_text:
                matched.append(orig)
        return matched
