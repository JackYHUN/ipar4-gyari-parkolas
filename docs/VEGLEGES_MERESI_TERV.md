# Végleges páros modellmérési terv

Rögzítés: 2026-10-12, a végleges kampány futtatása előtt. A futtató a változtatás nélkül használt `factory_yard.World` és `Coordinator` aktuális 0.18.0 verzióját vizsgálja; a konkrét források és konfigurációk SHA-256 azonosítója minden kampány elején és végén mentésre kerül. A régi `results/model-comparison` és −7,992 s eredménye külön történeti mérés, nem az új kampány eredménye.

## Előre rögzített esetek

| Paraméter | Referencia | Nagyobb terhelés |
|---|---:|---:|
| Konfiguráció | `config/course-reference.json` | `config/course-reference-high-load.json` |
| Termelési felkínálási időköz | 9 s | 3 s |
| Kohorsz / aktív felső korlát | 6 / 6 autó | 6 / 6 autó |
| Parkolóhely / kezdetben foglalt | 16 / 4 | 16 / 4 |
| Zebra / konfliktuszóna | 2 / 1 | 2 / 1 |
| Időlépés / névleges érzékelési periódus | 0,05 / 0,1 s | 0,05 / 0,1 s |
| Megfigyelési felső határ | 600 s | 600 s |
| Kiosztási szabály | `fixed`, `nearest` | `fixed`, `nearest` |
| Független futáspárok | 30 | 30 |
| Seedek | 1001–1030 | 1001–1030 |

A konfigurációk a `default.json` pontos másolatai, csak a felkínálási köz, a `production_limit=6`, a `parking_dwell=601` és az `automatic_pedestrians=false` tér el. Mind a hat autó a horizont végéig a kohorszban marad; nincs termelési utánpótlás vagy kiadás. A 600 s felső megfigyelési határ. Ha már mind a hat felkínált autó beparkolt, a számolás lezárható: a parkolási idő és úthossz ezután változatlan, a kohorsz nem kap utánpótlást és a parkolók nem szabadulhatnak fel. A befejezett kohorsz utáni érzékelő- és gyalogosmegfigyelések ekkor nem készülnek el; ez számítási lezárás, nem az aktív járművek fizikájának vagy megfigyelőinek egyszerűsítése. A 3 s eset nagyobb egyszeri érkezési terhelést jelent, nem tartós üzem vagy kapacitásmaximum vizsgálatát.

## Párosítás és külső események

Egy seed és egy konfiguráció alatt a `fixed` és `nearest` azonos kezdeti foglaltságból indul. A felkínálások előre rögzítettek: `CAR001`…`CAR006`, referencia esetben 0, 9, 18, 27, 36, 45 s, a nagyobb terhelésnél 0, 3, 6, 9, 12, 15 s. A runner a modell tényleges pufferében vagy már felvett autójában tárolt időből ellenőrzi ezt, ezért a koordinátor által befolyásolható udvari beléptetési idő nem helyettesíti a külső felkínálást.

A modellen belüli automatikus gyalogosgenerálás kikapcsolt. A teljes, két zebrát érintő érkezési eseménysor mindkét stratégiától és `World`-állapottól függetlenül készül el, saját `random.Random(seed XOR 0xC0A718)` generátorral. Zebráról zebráról a konfiguráció sorrendjében az első érkezés `U(7,14)` s, a további érkezési közök `U(50,65)` s mintákból származnak, felfelé kerekítve 0,05 s-os időlépésekre. Az események ezután időpont és zebraazonosító szerint rendezettek. A ritkább sor csökkenti az átfedő kérések esélyét; ez a mérés gyalogosterhelésének explicit feltétele, nem a demó automatikájának reprodukciója.

A tényleges szimulációzáró időpontig (beleértve annak határát) ütemezett minden érkezésnél ellenőrizzük a gyalogoslétrehozást. A `World.control('pedestrian')` aktív gyalogos esetén egy új kérést eldobhatna; bármely eldobott megfigyelt esemény `all_ped_arrivals_created_in_observation_window=false` állapotot és hibás párosítás miatti leállást eredményez. A teljes 600 s-os külső manifest megmarad. A lezárt kohorsz utáni események külön `completed_cohort_after_run_end_not_observed` jelölést kapnak, létrejöttükre nem állítunk megfigyelést. Mivel a két stratégia eltérő időpontban végezhet, a tényleges érkezéseket a két futás közös megfigyelési ablakában párosítjuk; egyik futás későbbi megfigyelt eseménye a másiknál már lezárt kohorsz utánra eshet. A gyalogosok tényleges átkelési kezdete, várakozása és befejezése a kooperatív szabály és járműhelyzet miatt endogén, tehát eltérhet a két stratégiánál. Csak az érkezés előzetesen közös exogén esemény.

A helyi érzékelő soros véletlenfolyama a látható járművek száma szerint fogy. Ezért pusztán azonos seed nem jelentene párosan azonos mérési hibát. Kizárólag az új kísérleti runner cseréli a `sensor_rng` és a megjelenítési célú `navigation_rng` szolgáltatóját az időlépés, az észlelő és az észlelt autóazonosító, valamint az eredeti mintavételi hívási hely alapján kulcsolt közös egyenletes zajmezőre. A hívási hely megkülönbözteti az x/y/irányszög mintákat; a seed és a kulcs SHA-256 alapján képzett 53 bites egyenletes értékét ugyanarra a korlátra skálázzuk. A helyi pozícióhiba változatlanul ±0,1 m. A forráskód hash védi a hívási hely állandóságát; ismeretlen hívási környezetnél a runner leáll.

Azonos megfigyelési kulcs azonos zajt kap mindkét futásban. A ténylegesen mintázott kulcshalmaz eltérhet, mert a közelség és az udvari beléptetés endogén. A párok exportja külön közli a közös és csak egyik futásban mintázott kulcsok számát; minden közös kulcs értékegyezését ellenőrizzük. Külön 12000 kulcsos determinisztikussági és ±0,1 m korlátellenőrzés készül. A zaj kapcsolásának változtatása explicit kísérleti módszertan; az alkalmazás modellforrását és valódi MQTT-futtatóját nem változtatja meg.

## Válaszváltozók és statisztikai egység

Elsődleges mutató az egy autó termelési felkínálásától a parkolásáig eltelt idő (s), az udvaron kívüli pufferben várakozással együtt. Másodlagos mutató a parkolásig megtett úthossz (m). A páron belüli különbség minden azonos, mindkét futásban befejezett autóra `nearest − fixed`; hat autó különbségének átlaga adja a futáspár értékét. A negatív érték a `nearest` javára mutat.

Egy seedhez tartozó teljes futáspár a független megfigyelési és újramintázási egység. Konfigurációnként a 30 páros átlagot 10000-szer mintázzuk visszatevéssel, `bootstrap_seed=90210` mellett. A 95%-os percentilisintervallum a rendezett bootstrap-átlagok 2,5% és 97,5% rendstatisztikája, a meglévő `compare.interval` megvalósítása szerint. Az egyes autók, telemetriaüzenetek vagy időlépések nem számítanak új független megfigyelésnek. A két konfiguráció külön elemzés, a köztük ismételt seedekből nem képzünk 60 független párból álló összevont intervallumot.

Befejezetlen autók és ütközések minden futásban külön szerepelnek, nincsenek csendben kizárva. Hiányos befejezésnél az idő- és úthosszkülönbség a mindkét stratégiában befejezett autókra feltételes; ezt nem értelmezzük teljes kohorszra vonatkozó előnyként. A 600 s végéig el nem végzett utakat és a futás lezárásakor fennálló járműállapotokat az autóexport is tartalmazza. Egyetlen ütközés is meghiúsítja az ütközésmentességi állítást.

## Ellenőrzés és export

Minden 0,05 s-os időlépés után ellenőrizzük: egy helyhez legfeljebb egy autó tartozik; egy autóhoz legfeljebb egy központi helyfoglalás; a foglalási azonosítók egyediek; kezdetben foglalt vagy blokkolt helyre nincs kiosztás; a modell és központ autó/hely/foglalási azonosítója egyezik; minden aktív modellbeli zónaengedélyhez azonos központi tulajdonos és engedély tartozik. A modell független geometriai ütközésvizsgálatát változtatás nélkül használjuk.

A nyers futások JSON- és CSV-exportja, a nyers párok JSON- és CSV-exportja, az autóeredmények CSV-je, az előre generált gyalogosütemezések, minden esemény és a központ döntési naplója megőrzött helyi műtermék. Az összesítés tartalmazza a verziót, Python-verziót és futtatóelérési utat, UTC kezdő/záró időt, futási időt, seedeket, egységeket, bootstrap-paramétereket, befejezetlen és ütközésszámokat, valamint a futtatás előtti/utáni forrás- és konfigurációhash-eket.

Előzetes technikai pilot: egy pár/konfiguráció (seed1001), teljes 600 s horizonttal, külön `pilot/` mappában, a végső 30 pár statisztikájába nem kerül. Ennek mért futási ideje 91,207 s / négy modellfutás volt. Erőforrástakarékosság miatt a végső kampány előtt rögzítettük a befejezett kohorsz utáni számítási lezárást. A `pilot-early-stop/` ismételt, két konfigurációs seed1001 vizsgálat minden autó parkolási idejét, udvari idejét, úthosszát, parkolási időpontját és foglalását pontosan a teljes horizontú pilothoz hasonlítja. A forrásmodell és konfiguráció változatlanságát is ellenőrzi, továbbá igazolja, hogy a teljes pilotban sincs kiadás. Bármely célmutató eltérése blokkolja a kampányt; a fő futtató csak ugyanazzal a hash-készlettel enged indulást, amellyel az ellenőrzés megfelelt.

A végső kampány szekvenciális, egy folyamatban fut, `.venv` Pythonból, munkaterületen belüli TEMP/TMP mellett. Futási parancs a `projekt` könyvtárból: `.venv\Scripts\python.exe -m experiments.course_comparison`; az early-stop ellenőrző pilothoz `--pilot` kapcsoló. A végső futási idő becslése az új pilot után készül.

## Értelmezési határ

A mérés az aktuális modellkód kiosztási szabályait vizsgálja memórián belüli determinisztikus protokollkézbesítéssel. Nincs MQTT-broker, hálózati jitter vagy csomagvesztés; a mérés nem az alkalmazás valós hálózati adatútjának tesztje. Az ismert útvonal, ideális poliláncmozgás, kooperatív gyalogos és korlátozott virtuális érzékelő feltétele szűkíti az érvényességet. A bootstrap a modell seedek közötti változását becsüli az adott forgatókönyvben, és nem valódi gyári hatásbizonytalanságot. Semmit nem publikálunk hálózatra.
