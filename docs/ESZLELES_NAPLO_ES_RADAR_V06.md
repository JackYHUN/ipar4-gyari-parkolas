# V06: tartós észlelési napló és az autó szemszögéből készülő nézetek

A 0.6-os változat az autó teljes rögzített útjához megőrzi a kijelző által ténylegesen fogadott virtuális érzékelőcsomagokat és a hozzájuk tartozó vezérlési döntést. Ugyanaz a csomag élőben és az útelőzmény idővonalán is megjeleníthető, radarszerű objektumnézettel és szintetikus elölnézettel. A cél annak ellenőrizhetősége, hogy egy adott időpontban milyen megfigyelés állt rendelkezésre, és a vezérlő milyen korlátozás alapján lassított vagy állt meg.

## Adatút és az adatok eredete

A lánc a szimulált folyamatból, a virtuális érzékelésből, az MQTT-publikálóból, a helyi közvetítőből, a kijelző feliratkozójából, az SQLite-archívumból és a böngészős nézetből áll. A napló a kijelző által feldolgozott MQTT-üzeneteket őrzi; az üzenetvesztés előtti, csak a szimulátorban létező megfigyelést nem állítja elő utólag. A topicok futásonként a `yard/<run_id>/` előtag alatt különülnek el. A normál és hibakori adatút saját UML-szekvenciadiagramja a [perception_recording.puml](perception_recording.puml) forrásban található.

| Adat | Forrás és szerep | Lényeges mezők |
| --- | --- | --- |
| Normál járműtelemetria | `telemetry/<car_id>`; a mozgáslépés utáni idealizált járműhelyzet és állapot, valamint az előtte mintavételezett érzékelőcsomag. | `sim_time`, `seq`, `x`, `y`, `speed`, `heading`, `state`, `sensors`, `control`. |
| Érzékelőcsomag | `sensors.source=virtual_onboard_bundle`; korlátos zajú saját helyzet, útvonalvetítés, ideális odometria és a mintavételezett közeli környezet. | `schema_version=1`, `sample_sim_time_s`, `pose`, `reference_pose`, `guidance`, `odometry`, `environment`, `age_s`, `fresh`, `fault_active`. |
| Vezérlési bizonyíték | `control.source=ideal_route_controller`; a már végrehajtott döntés egyszeri rögzítése. A naplózás nem futtatja újra a korlátozásokat számító eljárást. | Célsebesség, előző és következő sebesség, alkalmazott gyorsulás, megállási cél és hátralévő távolság, indok, cél- és eredményállapot, zónaengedélyek, kapcsolatállapot. |
| Hibakori megfigyelői diagnosztika | `diagnostics/perception/<car_id>`; az aktuális vezérlési döntés és az utolsó, elavuló érzékelőcsomag továbbítása külön témán. | `source=simulation_controller_diagnostics`, `position_source=post_step_simulation_reference`, `seq=null`, `sensors`, `control`. |

A hibakori diagnosztika X/Y helyzete a szimuláció referenciaállapota, és külön megfigyelői bizonyíték. A koordinátor erre a témára nem iratkozik fel, ezért az érzékelőhiba nem kap új, valódi mérésként kezelt helyzettelemetriát. Az érzékelőcsomag referenciahelyzete továbbra is a régi mintavételi hely, miközben a diagnosztikai járműhelyzet és a vezérlési döntés időpontja előrehaladhat. MQTT-kiesés alatt a kijelzőhöz sem normál telemetria, sem ilyen diagnosztika nem érkezik; az archívumban adatrés marad.

A normál telemetria és a hibakori diagnosztika QoS 0-val kerül publikálásra. A megőrzött fogadott minták teljessége nem jelent garantált MQTT-veszteségmentességet. Az alapmintavétel 0,1 szimulációs másodperc, a virtuális érzékelési hatótáv 18 m, a frissességi határ 0,5 s. A saját és a megfigyelt járművek helyzajának tengelyenkénti ±0,1 m korlátja modellfeltevés; a saját irányzaj ±1°. A pontos mezőértelmezést a [V05 érzékelőleírás](ERZEKELOK_ES_FEJLESZTES_V05.md) adja meg.

## Órák, frissesség és döntési idő

Az érzékelési idő, a döntési idő és a fogadási idő külön jelentést hordoz. A rosbag2 rögzítési és visszajátszási megoldása is elkülöníti a szimulációs órát és a rendszerórát, valamint támogatja a rögzített üzenetek időbeli lejátszását [1]. A prototípus e rögzítési elveket saját SQLite/JSONL adatúttal alkalmazza; ROS telepítése nem szükséges.

| Mező | Értelmezés |
| --- | --- |
| `sim_time` | A telemetria vagy diagnosztika modellideje, szimulációs másodpercben. |
| `sensors.sample_sim_time_s` | Az eredeti érzékelőminta ideje; a csomag a mozgáslépés előtt keletkezik. |
| `sensors.age_s` | Az eredeti mintavétel óta eltelt szimulációs idő. Hibakor nő, a minta helyzete és környezete változatlan. |
| `control.decision_sim_time_s` | A már végrehajtott vezérlési döntés modellideje. A `motion_interval_s` a mozgáslépés hossza. |
| `utc`, `details.mqtt.received_utc` | Az MQTT-fogadási callbackben rögzített gépi UTC idő. |
| `details.mqtt.latency_ms` | A küldéstől a fogadási callbackig mért alkalmazási kézbesítés azonos gép monoton számlálóival. Nem teljes érzékelés–fékezés reakcióidő. |

A modell `fresh` mezője az adatkort és a frissességi küszöböt hasonlítja össze. Aktív érzékelőhiba esetén a felület már a küszöb lejárta előtt elavult megfigyelésként mutatja a csomagot; az archívum elavultkeret-számlálója is figyelembe veszi a `fault_active` értékét. Lejátszáskor a frissesség az adott mentett időpontra vonatkozik. Adatrésben az utolsó fogadott észlelés marad, külön figyelmeztetéssel; a program nem interpolál köztes objektumokat.

Az Autoware lokalizációs architektúrája időbélyeges helyzet-, sebesség- és gyorsulásbecslést, bizonytalanságot és diagnosztikát tárgyal [2]. A jelenlegi zajkorlát nem mért kovariancia vagy konfidenciaintervallum, a helyzetbecslés pedig nem vezérli az idealizált pályakövetést.

## Tartós archiválás és számlálók

Minden feldolgozott járműtelemetria vagy hibakori diagnosztika fontos mezői a `trip_samples` táblába kerülnek. A hozzá tartozó teljes `sensors` JSON-csomag UTF-8 kódolás után, zlib 3-as tömörítési szinttel a `trip_perception` táblában marad meg, azonos mintaazonosítóval. A `control` csomag a tömör útadat részleteiben tárolódik. A későbbi sémaelemeket az archiváló nem válogatja ki a szenzorcsomagból.

A teljes fogadott út és észlelési archívum független a 200 000 legújabb részletes minta ablakától és a nyers MQTT JSONL körkörös keretétől. A már feldolgozott csomagokat a nyers fájlrotáció nem törli. A tranzakciók 100 bejegyzés után vagy 0,5 s elteltével a következő üzenet feldolgozásakor véglegesülnek; lekérdezés, export és rendezett leállítás szintén kiírja a függő változásokat.

| Összegző mező | Jelentés |
| --- | --- |
| `perception_samples` | Megőrzött fogadott szenzorcsomagok száma. Ismételt régi megfigyelés és sikeres első mérés nélküli csomag is szerepelhet benne. |
| `perception_distinct_samples` | A sikeres, mintavételezett csomagok különböző `sample_sim_time_s` időpontjainak száma. |
| `perception_stale_frames` | Sikeres korábbi mintát tartalmazó, de elavultnak vagy aktív hibásnak jelölt keretek száma. |
| `perception_coverage` | A megőrzött csomagok aránya a kiválasztott autó rögzített fogadott mintáihoz képest. Nem felismerési pontosság és nem az összes előállított mérés aránya. |
| `perception_complete_received_archive` | Van fogadott minta, mindhez megmaradt csomag, és a régi archívum ismert korábbi adatvesztése nulla. |
| `perception_compressed_bytes`, `perception_uncompressed_bytes` | Futásszintű tömörített és tömörítetlen szenzorpayload-méret; nem a teljes SQLite-adatbázis mérete. |

Korábbi adatbázisból csak a még megőrzött részletes telemetriához tartozó tényleges szenzorcsomag olvasható vagy migrálható. A már törölt csomag hiánya megmarad. A régi pozícióból, a mai térképből vagy az élő autókból nem készül utólag feltételezett észlelés.

A tartós archívum lemezmérete folyamatos futásnál növekszik. A példafutás 5038, két autóhoz tartozó csomagjának payloadja 6 920 165 bájtról 3 334 141 bájtra tömörült, az eredeti méret körülbelül 48%-ára. Ez egy konkrét forgatókönyv eredménye: az objektumok száma és az adatok változása befolyásolja a tömörítést. Az SQLite sorai, indexei, vezérlési részletei, a WAL és a nyers fájlok ezen felül igényelnek helyet. A tömörítés nem korlátozza a teljes archívum növekedését.

## Élő nézet és visszajátszás

A virtuális radarnézet a mintavételkori referenciahelyzetet teszi középre, és a mintavételezett zajos irány szerint forgatja a környezetet; előre felül, jobbra a pozitív irányszög. A kör és az objektumok távolsága méterben szerepel. A közeli autók észlelt helyzetek, a zebrahelyek ismert térképi koordinátákhoz társított mintavételezett gyalogosállapotok. Utóbbiak nem gyalogospozíciók és nem radar-visszaverődések; a zebra közepe távolabb is lehet, mint a hatótávban észlelt gyalogos.

A szintetikus elölnézet a csomag megfigyeléseinek előretekintő 90°-os geometriai vetülete. A jelképek mérete szemléltető, a segédsík nem felismert útburkolat, a szaggatott irány az ismert útvonalé. Nem készül kamerafelvétel, videó, pixelalapú felismerés vagy fotorealisztikus jelenet. Az Autoware észlelési architektúrája külön kezeli a kamera-, LiDAR- és radarbemeneteket, a térképi információt és az objektumészlelési eredményeket [3]; a prototípus jelenlegi objektumlistája ezek közül csak a virtuális megfigyelési eredmény szemléltetésére szolgál.

A nézet mellett a legközelebbi észlelt autó, a vezérlő célsebessége, az ideális fékút és reakcióráhagyás, illetve a döntés oka olvasható. A fékút `v²/(2b)`, a ráhagyás `v·mintavételi_periódus`; `v` a mozgáslépés előtti sebesség, `b` a modell állandó fékezése. A két érték nem igazol valódi járműbiztonsági távolságot. A vezérlő megállási korlátozása és diszkrét mozgáslépése külön, a `control` mezőkben vizsgálható.

A teljesút-nézet legfeljebb az első 20 000 fogadott mintát tölti be a böngészőbe, 2000 mintás API-lapokkal. A kiválasztás elején rögzített `until_id` miatt az élőben érkező későbbi minták nem változtatják meg a betöltött tartományt. A korlát elérését a felület jelzi. A teljes autónkénti CSV/JSON-export kötegekben olvassa az összes megőrzött mintát, a böngésző keretétől függetlenül; az export kezdetén rögzített felső azonosítóig tart.

Az **Állókép SVG** a kijelzett radar- és elölnézetet menti, a kapott szenzorcsomaggal és döntéssel beágyazott metaadatként. Az **Ez a minta JSON** ugyanazon keret adatait és a nézet kontextusát adja vissza, például hogy archív nézetből vagy adatrésben tartott állapotból származik. A helyi HTTP-exportvégpont a böngészőben elkészített állókép vagy keret ellenőrzött tartalmát letölthető mellékletként adja vissza; külön fájlt nem tárol, és nem futtat új renderelőt. A teljes út exportjában minden tárolt mintához a `sensors`, `control` és `details` mezők tartoznak; a CSV-ben a beágyazott csomagok JSON-szövegként szerepelnek.

## Követelmény–megvalósítás–bizonyíték

Az igazolási szempont a megfigyelések származása és időbeli visszakereshetősége. Az alábbi érvelés a jelenlegi véges tesztkészlethez és a megvalósított adatúthoz kötött.

| Követelmény | Megvalósítás | Bizonyíték |
| --- | --- | --- |
| Nyers rotáció után is visszakereshető legyen, mit tartalmazott a fogadott keret. | Mintaazonosítóhoz kötött teljes zlib szenzorcsomag a tartós SQLite-archívumban. | Kis részletes ablakkal és nyers kerettel végzett tesztek; csomagok és ismeretlen mezők pontos visszaolvasása. |
| Régi észlelés ne váljon új méréssé hibakor vagy visszajátszáskor. | Változatlan `pose`, `environment` és mintavételi idő; növekvő kor, külön diagnosztikai forrás és felületi jelzés. | Valódi MQTT-hibainjektálás: változatlan régi minta, több új döntési idő, fékezési megállás, majd új mintával helyreállás. |
| A képi nézet a fogadott csomagból készüljön. | Közös érzékelőcsomag az élő és archív radarnézethez, illetve elölnézethez; hiányzó csomagnál üres nézet. | A megjelenítő kód forrásellenőrzése; a konkrét felületi ellenőrzések külön verifikációs jelentésben szerepelnek. |
| Reset és lapozás után se keveredjenek futások vagy minták. | `run_id` + `car_id`, változatlan lapozási felső határ, külön mentett konfiguráció és streamexport. | Régi futás olvasása reset után; CSV/JSON és lapozott HTTP-adatok egyezése, export közbeni futásváltás egységtesztje. |

## Ellenőrzött eredmények és korlátok

A `verify_units.py` legutóbbi futtatása **140 sikeres egységtesztet** adott, köztük hét célzott HTTP állóképexport-teszttel. A külön `python -m tests.perception_mqtt_smoke` valódi helyi MQTT 3.1.1 közvetítővel, külön munkafolyamatokkal és HTTP-történeti API-val ellenőrizte a láncot. A 2026. október 5-i `69a5a1e0d503` futásban két autó beparkolt, modellezett ütközés nélkül. A CAR001 archívuma **2485 fogadott keretet**, **2425 különböző érzékelési mintavételi időpontot** és **60 hibakori diagnosztikai keretet** tartalmazott. A 215 nyers fájlrész lecserélése ellenére minden feldolgozott keret szenzorcsomagja megmaradt; a lapozott HTTP-, CSV- és JSON-adatok pontosan egyeztek, és reset után a régi történet továbbra is olvasható volt.

A beépített böngészőből végzett natív JSON- és SVG-letöltés külön ellenőrzése is sikeres. A két letöltés metaadatai azonosak, az érzékelőcsomag, a vezérlési döntés és a fogadási UTC az archivált MQTT-mintával egyezik. A [letöltési ellenőrzés](../results/ui/perception-download-check.json) a 0,15 s érzékelési idő és a 3,55 s döntési idő külön megőrzését is rögzíti.

A tényleges mérési bizonyíték hordozható másolata a [rendszerteszt összegzése](../results/PERCEPTION_MQTT_RENDSZERTESZT.json), a [CAR001 teljes JSON-archívuma](../results/mqtt-proof/CAR001-perception.json), valamint a [CSV-exportja](../results/mqtt-proof/CAR001-perception.csv). Az eredeti futás a `results/perception-smoke-20261005-225201/` könyvtárban marad meg. A 100% itt fogadott csomaglefedettség; nem 100%-os felismerés és nem hálózati veszteségmentességi ígéret.

A környezetérzékelés 360°-os, sugár alapú modell takarás és tárgyazonosítási hiba nélkül. Nincs nyers radarmérés, Doppler-sebesség, LiDAR-pontfelhő, kameraképfeldolgozás, időjárás vagy megvilágításmodell. A másik autó sebessége skalár; irányított sebességvektor hiányában általános ütközésig hátralévő időt, TTC-t a nézet nem számol. Az útvonalvetítés és a helyzaj diagnosztika; a kormányzás továbbra is ideális vonallánc-követés. A teszt nem bizonyít valós autógyári vagy közúti biztonságot.

## Következő, indokolt fejlesztések

A jelenlegi archívum összehasonlítható mérési alapot ad a továbblépéshez. A legfontosabb lehetőségek a következők:

1. **Döntési és mozgási idősorok közös grafikonja.** A megfigyelés kora, a célsebesség, a tényleges sebesség, a gyorsulás és a megállási távolság egy idővonalon megmutatná a fékezési folyamatot. Az idősor az archivált mezőkből számítható, mérési pontok pótlása nélkül.
2. **Észlelési minőség összegzése.** A helybecslés referenciához viszonyított eltérése, a mintaközök ingadozása és az elavult időszakok aránya futások vagy véletlenmagok között összehasonlítható. Referenciaállapotot használó mutató külön diagnosztikai eredményként szerepelne, nem fedélzeti tudásként.
3. **Látómező és takarás tényleges modellezése.** A geometriai akadályok, rejtett autók és gyalogosok befolyásolnák a szenzorcsomagot és a helyi fékezést. Ehhez új tesztesetek és ismeretlen területet is jelző nézet szükséges; a mostani elölnézeti 90°-os szűrés önmagában csak megjelenítés.
4. **Kormányzás és lokalizációs drift kísérleti változata.** A becsült helyzet valódi visszacsatolással hathatna a pályára. A lekerekített kanyarok, a jármű kinematikája és a zónageometria új igazolást igényelne.
5. **Exportált bemutatóvideó a mentett keretekből.** A már rögzített út és észlelés külön rendereléssel videóvá alakítható. Ez szintetikus visszajátszási videó lenne; valódi kamerafelvételhez külön kamera- vagy képszintű szimulációs adatforrás szükséges.

## Irodalom

[1] ROS 2 Project, “rosbag2 — Recording, splitting files, simulation time and replay,” *rosbag2*, rolling ág, szoftver és frissülő dokumentáció. [Online]. Elérhető: [hivatalos rosbag2 README](https://github.com/ros2/rosbag2/blob/rolling/README.md). Hozzáférés: 2026. okt. 5.

[2] Autoware Foundation, “Localization component design,” *Autoware Documentation*, frissülő webdokumentáció. [Online]. Elérhető: [lokalizációs architektúra](https://docs.autoware.org/main/design/autoware-architecture-v1/components/localization/). Hozzáférés: 2026. okt. 5.

[3] Autoware Foundation, “Perception component design,” *Autoware Documentation*, frissülő webdokumentáció. [Online]. Elérhető: [észlelési architektúra](https://docs.autoware.org/main/design/autoware-architecture-v1/components/perception/). Hozzáférés: 2026. okt. 5.
