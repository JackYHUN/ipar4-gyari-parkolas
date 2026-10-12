# Gyártósori kijárattól a parkolóhelyig: rendszerhatár és igazolás

## A feladat és a rendszerhatár

A vizsgált rendszer egy autógyári készjármű-udvar kisméretű, kétdimenziós modellje. A gyártósor kijáratához érkező kész autók parkolóhelyet kérnek, a központ által kiosztott útvonalon haladnak, két gyalogos-átkelőnél helyi érzékelésből döntenek a megállásról, és egy közös konfliktuszónába csak engedéllyel lépnek be. A cél a parkolóhely-kiosztás és az adatút működésének ellenőrzése, továbbá két kiosztási szabály összevetése.

A rendszerhatár tartalmazza a járművek mozgásmodelljét, a virtuális érzékelőket, a járművek helyi vezérlőjét, a központi koordinátort, a helyi MQTT-közvetítőt, valamint a műszerfalat és naplózást. A valódi autógyári hálózat, a gyártósor részletes modellje, a kameraalapú felismerés, a fedélzeti gépjármű-elektronika és a közúti forgalom kívül esik a határon. A 140 × 90 méteres terület szemléltető térkép, nem egy konkrét üzem felmérése.

Az alapváltozat legfeljebb hat aktív autót, tizenhat parkolóhelyet, két zebrát és egy konfliktuszónát kezel. A parkolóban állás utáni kiadás egyszerűsítés: a jármű eltűnik a helyről, majd a hely felszabadulását külön virtuális foglaltságérzékelés igazolja. A teljes kihajtási útvonal további fejlesztés.

## CPS-komponensek besorolása

| Osztály | Modellbeli elem | Szerep |
|---|---|---|
| Fizikai | Járművek, gyalogosok, úthálózat és parkolók modellje | Időben változó helyzet, sebesség, fizikai kiterjedés és foglaltság. Ezek szimulált elemek. |
| Jelátalakító | Virtuális fedélzeti akadályérzékelő és parkolófoglaltság-érzékelő | A folyamatállapotból mintavételezett mérési jelet készít. Az igazságállapot és a vezérlő által használt megfigyelés elválik. |
| Logikai | Helyi fékezés, parkoló- és zónakoordinátor, MQTT-közvetítő | A mérésekből döntést készít, foglalást tart nyilván, illetve üzeneteket továbbít. |
| Emberi | Műszerfal kezelője; kooperatív gyalogosmodell | Demóforgatókönyvet és hibainjektálást kezdeményez, illetve érzékelhető gyalogoshelyzetet hoz létre. |

A koordinátor a teljes megjelenítési állapotot nem olvassa. Parkolófoglaltságot, járműtelemetriát, kérelmeket és azonosított eseményeket kap. A műszerfal számára a szimulátor külön megjelenítési állapotot közöl MQTT-n; ezt a koordinátor döntéseiben nem használja.

## Architektúra és adatút

A komponensek kapcsolatát az 1. ábra forrása, a `components.puml` írja le. Az adatút: szimulált folyamat → virtuális érzékelő/publikáló → MQTT-közvetítő → koordinátor/feliratkozó → döntés → járműparancs, valamint külön ágon a műszerfal. A közvetítő tényleges TCP-kapcsolatot kezel a laptopon, nem egy függvényhívással helyettesített üzenetsor.

A 2. ábra forrása, a `parking_sequence.puml` a parkolófoglalás és a felszabadítás üzenetsorrendjét mutatja. A 3. ábra forrása, a `car_states.puml` a helyi vezérlés fontos állapotait rögzíti. Mindhárom ábra saját szerkesztés; a végleges jelentésbe renderelt, sorszámozott képként és szöveges kereszthivatkozással kerülhet.

A 0. szintű adatfolyamot a 4. ábra forrása, a `dataflow.puml` foglalja össze. Az érzékelt adatok, a hálózati üzenetek és a járműre visszaható mozgási döntés külön nyilakat kap. Ez az ábra is saját szerkesztés.

Az AMQTT közvetítő alapértelmezetten a `127.0.0.1:18883` címen fut. A Paho kliensek MQTT 3.1.1 üzeneteket küldenek. A kérések és az engedélyek QoS 1 szintűek, ezért ugyanazon kérelem újrakézbesítése előfordulhat. A kérelemazonosítóhoz tartozó válasz ismétlése nem hozhat létre második foglalást. Minden futás külön `run_id` értéket és `yard/<run_id>/` témaelőtagot használ; korábbi futás üzenete nem módosíthatja az aktuális állapotot.

## Jelek és mérnöki feltételezések

| Jel | Mértékegység | Mintavétel / forrás | Értelmezés |
|---|---|---|---|
| Járműpozíció | m | 0,1 s szimulációs idő; virtuális telemetria | A jármű középpontja a síkbeli térképen; a fedélzeti akadálymérés ±0,1 m korlátú zajt tartalmaz. |
| Járműsebesség | m/s | 0,1 s szimulációs idő; virtuális telemetria | A mozgásmodell pillanatnyi értéke. |
| Parkolófoglaltság | logikai | 0,1 s szimulációs idő; virtuális érzékelő | Fizikai foglaltság és üzemeltetői lezárás; eltér a logikai foglalástól. |
| Helyi akadályadat | m / állapot | A mozgásmodell helyi érzékelési ciklusa | Hatótávolságon belüli akadály vagy gyalogos; hibainjektálással elavulhat. |
| MQTT-kézbesítési idő | ms | Valós monotón küldési és fogadási idő | Egy laptop közös óráján mért alkalmazási kézbesítés, nem fizikai hálózati egyirányú késleltetés. |
| Parkolásig eltelt idő | s | Szimulációs események | A termelési felkínálástól a parkolásig tartó idő, a külső puffer várakozásával együtt. |
| Megtett út | m | Mozgásmodell integrált úthossza | Parkolásig megtett távolság. |

Az alapmozgás paraméterei: 4,4 m járműhossz, 1,8 m szélesség, legfeljebb 3 m/s sebesség, 1 m/s² gyorsulás és 2 m/s² fékezés. A helyi érzékelési hatótávolság 18 m, az érzékelési adat elavulási küszöbe 0,5 s. Ezek oktatási modellparaméterek, nem valamely gyártó hitelesített autóadatai. A saját jármű pozíciótelemetriája idealizált; a másik jármű helyi érzékelt helyzetén tengelyenként ±0,1 m korlátú egyenletes zaj szerepel, amelyet a követési korlát konzervatív ráhagyással kezel. A részletes helymeghatározási hibamodell nincs kidolgozva.

A zebra helye ismert térképadat. A gyalogos megjelenése és mozgása kooperatív: a járdaszegélyen várakozó gyalogos érzékelhető, és nem jelenik meg azonnal egy jármű belsejében. A jármű előre rögzített vonalláncon halad; a részletes kormányzási geometria és parkolási manőver nem része a modellnek. A vizsgálat eredményei e feltételezések mellett érvényesek.

## Szempont–facet érvelés: foglalás és frissesség

| Követelmény | Megvalósítás | Ellenőrizhető bizonyíték |
|---|---|---|
| Egy parkolóhelyre egyidejűleg legfeljebb egy érvényes logikai foglalás adható. | A koordinátor sorosan dolgozza fel a kérelmeket, és a helyet a válasz elkészítése előtt lefoglalja. | Két közeli kérelem külön helyet kap; duplikált kérelem ugyanazon foglalást kapja vissza. |
| Elavult parkolóérzékelésből nem hozható új foglalási döntés. | Monotón beérkezési idő és növekvő sorszám; az alapküszöb 2 valós másodperc. | Frissességi és sorrendcserés teszt. A régi üzenet nem frissíti a szenzor időbélyegét. |
| Időtúllépés önmagában nem szabadíthat fel fizikai területet. | Parkolóhoz egyező kiadási esemény és egy későbbi üres minta; zónához egyező kilépési esemény és teljes járművet kívül mutató telemetria kell. | Hibás azonosítós, időtúllépéses, részleges kilépéses és késői érzékeléses teszt. |
| A gyalogos miatti fékezés ne függjön központi válaszra várástól. | Helyi fedélzeti vezérlés és helyi virtuális érzékelés. | Gyalogoseseményes, érzékelőhiba- és kommunikációkimaradásos forgatókönyv; mozgási eseménynapló. |

Az időzítés két óratartományt használ. A fizikai szimuláció `sim_time` értéke gyorsítható vagy szüneteltethető. Az MQTT-frissesség és alkalmazási kézbesítés valós monotón órához kötött; gyorsításkor e két tartomány értékei nem hasonlíthatók közvetlenül. A parkolásig eltelt idő kezdete a termelési felkínálás, nem feltétlenül az udvarba való belépés. Az eseménynapló `yard_time_s` mutatója ezzel szemben az udvarba belépéstől a parkolásig számít; a különbség a termelési puffer várakozása. A C1-zóna sorrendi szabálya a térkép egyetlen keleti irányú bejáratára készült; több bejárat vagy kétirányú közlekedés esetén új konfliktus- és prioritásmodell szükséges.

Az alkalmazási időbélyegekhez `time.perf_counter_ns()` használatos, közös laptopórával. A metaadat az óramegvalósítást és a névleges felbontást is tárolja; az ellenőrzött Python 3.12/Windows környezetben QueryPerformanceCounter, 100 ns felbontás. A korábbi GetTickCount64-alapú `monotonic_ns()` óra itt 15,625 ms-os kvantálása miatt a kis késésekhez nem elegendő. A kijelzőben az utolsó legfeljebb 1000 fogadott üzenet küldési alkalmazáshívástól a fogadó callbackig eltelt idejének átlaga szerepel; ez nem a teljes szabályozási reakcióidő.

## Kísérleti terv és értelmezési korlát

A `fixed` szabály a legkisebb szabad parkolóazonosítót választja, a `nearest` szabály a legrövidebb előre definiált bejárási útvonalú szabad helyet. A páros összehasonlítás ugyanazt a véletlenmagot, érkezési rendszert és kezdeti állapotot használja. Elsődleges mutató a parkolásig eltelt szimulációs idő; a megtett út másodlagos mutató.

Az `experiments/compare.py` a rendszer ugyanazon mozgás- és koordinátormodulját determinisztikus üzenetkézbesítéssel futtatja. Nem ad a koordinátornak teljes igazságállapotot, azonban nem futtat MQTT-közvetítőt és nem mér hálózati teljesítményt. Az így kapott eredmény modellvizsgálat; az MQTT-adatút bizonyítéka a külön, tényleges közvetítővel futó demó és annak naplója. A vizsgálat véges, hat autós kohorsz: a további termelés leáll, a parkolási tartózkodás a 600 s megfigyelési horizontnál hosszabb. Ezzel a két szabály azonos felkínált munkamennyiséget kap. A demó alapbeállítása ezzel szemben folyamatos termelést és 45 s parkolási tartózkodást használ.

A rögzített vizsgálati idő végén még parkolásra váró autókat külön kell kimutatni. Az elsődleges páros időeredmény csak a mindkét szabály esetén elkészült azonos járművek összehasonlításából származik; ezért a befejezési arány külön közlendő, és a feltételes átlag nem jelent minden beérkező autóra érvényes időjavulást. A magonkénti átlagkülönbségre páros bootstrap 95%-os intervallum készül; ez a megadott modell és forgatókönyv véletlenszerűsége mellett értelmezhető.

## Hátralévő beadandóelemek

A helyi prototípus és modellkísérlet önmagában még nem a végleges leadási csomag. Szükséges a nyilvánosan vagy az oktató számára elérhető kódtár, a működést bemutató videó, a legalább ötoldalas műszaki PDF, a renderelt UML-ábrák, a szövegbeli ábrahivatkozások és a teljes IEEE-irodalomjegyzék. A jelentésben a valós MQTT-mérés és a determinisztikus modellkísérlet elkülönítve szerepeljen.

## Elsődleges műszaki források

- Eclipse Paho: *Python client documentation*. https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
- AMQTT: *Broker configuration*. https://amqtt.readthedocs.io/en/latest/references/broker_config/
- OASIS: *MQTT Version 3.1.1*, 2014. https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html

A fenti linkek fejlesztési forrásjegyzékek. A végleges jelentésben sorszámozott IEEE-bejegyzésekké alakítandók, szervezettel, címmel, verzióval/évvel és hozzáférési dátummal.
