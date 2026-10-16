# A fogadott fékezési döntés vizsgálata

Az élő érzékelőnézet és a teljesút-előzmény közös kártyája megmutatja, hogy a jármű az adott vezérlési lépésben fékezett, gyorsított, egyenletesen haladt vagy megállt. Az értelmezés a már naplózott `ideal_route_controller` döntésből származik; nem futtatja újra a vezérlőt. A megállási ok neve önmagában nem jelenti azt, hogy az autó már áll.

## Megjelenített adatok

| Érték | Eredet és jelentés |
|---|---|
| Sebesség előtte és utána | A döntés `speed_before_m_s` és `speed_after_m_s` mezője, m/s-ban. |
| Alkalmazott gyorsulás | A naplózott `applied_acceleration_m_s2`; negatív érték lassulás. |
| Megállási céltávolság | `stop_limit_remaining_m`, az adott mozgási lépés előtti útvonaltávolság, méterben. A null azt jelenti, hogy nincs kijelölt megállási cél. |
| Modellbeli fékezési tartalék | Megállási céltávolság mínusz ideális fékút. A cél már tartalmaz vezérlői ráhagyásokat; a reakcióutat nem vonjuk le másodszor. |
| Fékezési kapacitás kihasználása | `max(0, -alkalmazott gyorsulás) / névleges féklassulás × 100%`. Ez modellparaméterhez viszonyított arány. |

A kék sáv az ideális fékutat, a függőleges jel a megállási célt mutatja közös relatív távolságskálán. Negatív tartalék látható marad. Egy pozitív modellbeli tartalék nem bizonyít fizikai biztonságot, és nem egy akadály felületétől mért távolság.

## Érvényesség és mérnöki érvelés

| Követelmény | Megvalósítás | Ellenőrzés |
|---|---|---|
| Fékező autó ne jelenjen meg állóként pusztán a várakozási ok miatt. | A mozgásállapot a lépés előtti és utáni sebességből származik. | Külön mozgó fékezési, gyorsulási és nulla sebességet elérő minták. |
| Hiányos vagy hibás adatból ne keletkezzen látszólagos tartalék. | Szigorú típus-, tartomány-, forrás- és időellenőrzés; ismeretlen érték helyén gondolatjel. | Régi control, null, karakterlánc, NaN, Infinity, negatív távolság és jövőbeli döntés. |
| Ellentmondó döntésből ne számoljunk származtatott mutatót. | Az ideális fékút és a sebességváltozásból következő gyorsulás összevetése a naplóval; meglévő szenzorórák egyezésének vizsgálata. | Eltérő fékút, gyorsulás és mintaidő; a tartalék és kihasználás ilyenkor hiányzik. |
| Adatrésben ne látszódjon új megfigyelés. | Külön megőrzött döntés jelzés, a mentett értékek megtartásával. | Elavult érzékelés és archív adatrés; az előző állapot törlése hiányzó új döntésnél. |
| Az export visszaadja a vizsgált döntést. | A JSON és a passzív SVG metaadata a nyers control és sensors mellett a `braking_analysis` mezőt is tartalmazza. | A valódi exportfüggvény és a böngészőből letöltött JSON/SVG vizsgálata. |

A fékút ellenőrzése `v_előtte² / (2b)`, a gyorsulásé `(v_utána − v_előtte) / dt`. Az összevetés figyelembe veszi a hat tizedesre kerekített naplómezőket. A kártya frissességi jelzése a megjelenített érzékelőmintához kötődik; egy archív friss minta továbbra is történeti adat.

## Futtatható ellenőrzések

```powershell
node tests/frontend_braking_analysis.cjs
node tests/frontend_braking_panel.cjs
```

Ezek önállóan futnak a kódtárban, összesen 31 ellenőrzéssel. A teljes fejlesztői munkapéldányban a korábbi MQTT-archívumra is ellenőrző parancs:

```powershell
python -m experiments.verify_braking_panel
```

Ez külön `results/ui/braking-panel-check.json` nyugtát ír, a korábbi kiadási nyugtákat nem írja felül. A teljes fejlesztői regresszió 239 korábbi ellenőrzést is futtat; a mentett MQTT-út 2486 döntési keretét offline elemzi. A nyilvános kódtár a tömör nyugtát tartalmazza; ehhez a fejlesztői újrafuttatáshoz az eredeti érzékelőarchívum külön szükséges.

A statisztikai referencia továbbra is az október 12-i V18 modellkísérlet. A megvalósítás a megjelenítést és a mintaletöltés tartalmát bővíti; a mozgásmodell, a MQTT-koordinátor és a mérési konfigurációk eredeti lenyomatai megmaradnak. A beadási csomaghoz tartozó forráspéldány a `f625cfe5e85cf5a8a28943704e7e025b89b7b0a4` commit alatt visszakereshető.
