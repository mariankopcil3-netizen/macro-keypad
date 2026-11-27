# Mechanical design (Autodesk Fusion 360)

3D model šasi a 3D tisk pro **Fintr Deck**.

## Konstrukce

- **Šasi** — tisknutelné z PLA/PETG, spodní část s vyjímkou pro ESP32 devkit,
  stěny pod displej (otvor 284×76 mm modul 2.25"), dva otvory pod EC11
  enkodéry a mřížka 12 otvorů pro mechanické spínače.
- **Horní kryt** — kouřový akrylát/plexi (3 mm), lepený na horní hranu šasi
  nebo uchycený M2 šrouby; dává kontrastní „sklo" nad klávesami a displejem.
- **Výklopné nožičky (kickstand feet)** — zadní packy na zacvakávacím pantu
  (printed-in-place live hinge), naklápějí zařízení do pracovního úhlu.

## Obsah (bude doplněno)

| Soubor | Popis |
|--------|-------|
| `*.f3d` | nativní Fusion 360 projekt |
| `exports/step/*.step` | STEP pro CAD/CAM |
| `exports/stl/*.stl` | jednotlivé díly orientované k tisku |

## Doporučené nastavení tisku

| Parametr | Hodnota |
|----------|---------|
| Materiál | PLA (levné, tuhé) nebo PETG (odolnější) |
| Vrstva | 0,2 mm |
| Výplň | 20 % (gyroid/grid) |
| Okraje | 3–4 stěny |
| Podpěry | jen pod vyjímkou enkodérů, dle potřeby |
| Tolerance | **0,2 mm** na čepy pacek a zacvakávací pant |

## Sestavení

- M2.5/M3 šrouby do tavených zátek nebo heat-insertů
- lepidlo/šrouby pro horní akrylátové sklo
- zapojení dle [docs/technicka-prirucka.md](../../docs/technicka-prirucka.md)
