"""回答繁體化：只轉換「簡體專用字」。

OpenCC 的 s2t／s2twp 用在已經是繁體的文字上會改壞字（例如「干擾」→「幹擾」）。本機 LLM 大多直接輸出繁體，
所以只把 OpenCC STCharacters 中「簡體專用」的字逐字換成第一個繁體字（质→質、纤→纖）；
繁簡共用或本身是繁體寫法的字（干、里、面、后、秘…）一律不動。
"""
import functools
from pathlib import Path


@functools.lru_cache(maxsize=1)
def _table() -> dict[int, str]:
    import opencc

    folder = Path(opencc.__file__).with_name("dictionary")

    def pairs(name):
        for line in (folder / name).read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                key, values = line.split("\t", 1)
                yield key, values.split()

    st = dict(pairs("STCharacters.txt"))
    # 本身就是繁體寫法的字：某個簡體字的繁體候選，或台灣異體字表中的字（例如「秘」）
    traditional = {c for values in st.values() for c in values}
    traditional |= {c for key, values in pairs("TWVariants.txt") for c in [key, *values]}
    return {ord(k): v[0] for k, v in st.items() if len(k) == 1 and v and k not in v and k not in traditional}


def to_traditional(text: str) -> str:
    return text.translate(_table())
