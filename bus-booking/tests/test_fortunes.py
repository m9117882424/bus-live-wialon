"""Check the complete bilingual collection and its no-repeat selection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fortunes import FORTUNES, TRANSLATIONS, choose_fortune, fortune_tr

assert len(FORTUNES) == len(set(FORTUNES)) == 1000
assert len(TRANSLATIONS) == len(set(TRANSLATIONS.values())) == 1000
assert fortune_tr('Пусть дорога будет лёгкой!') == 'Yolunuz açık olsun!'
assert fortune_tr('Старое неизвестное пожелание') == 'Старое неизвестное пожелание'

in_use = set()
for _ in range(1000):
    value = choose_fortune(in_use)
    assert value not in in_use and fortune_tr(value)
    in_use.add(value)
assert in_use == set(FORTUNES)
assert choose_fortune(in_use) in in_use  # Booking remains available after exhaustion.
print('PASS: 1000 distinct bilingual wishes, legacy translations, non-repetition and exhaustion')
