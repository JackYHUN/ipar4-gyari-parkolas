# Győri Audi-telephely: források és a szimuláció térképi alapja

Ellenőrzés dátuma: 2026-10-04. A forráskutatás célja egy saját, egyszerűsített oktatási térkép megalapozása, amelyen a kész járművek MQTT-alapú parkolóhely-kiosztása és közlekedése vizsgálható. A fő épülettömbök egymáshoz viszonyított helyzete nyilvános Audi-térképből származik; a modell belső úthálózata, parkolóhelyei, zebrái, járműmozgása és méretei mérnöki feltevések.

## Ellenőrzött térképi forrás

**[1] AUDI HUNGARIA Zrt., „Nyílt nap / Open Day, 2023. július 8.”, Programfüzet, 2023.**

[Hivatalos PDF](https://audi.hu/content/dam/audi-hu-site/hu/hu/news/2023/audi-hungaria-ny%C3%ADlt-nap/Programf%C3%BCzet.pdf). A térkép a PDF **2. oldalán** található, és a kutatás során képként is ellenőrzésre került. A funkciókhoz a 7., 10-11., 13-19. és 21-23. oldal programleírásai szolgálnak további támpontként. A helyi ellenőrzési másolat: `references/Audi_Hungaria_Nyilt_nap_2023.pdf`; SHA-256: `bf1536f16accc66094ef72f94306742a617f3df46b19e576fa6fe34d669fb80c`.

| Épület vagy tömb | A forrásábrán megfigyelt relatív hely | A programfüzettel alátámasztott funkció |
| --- | --- | --- |
| G1, G10, G20 | A nagy bal oldali gyártási tömb | Motorgyártás, motorszerelés |
| G7, G18 | A bal tömb közepe, illetve alsó széle | Központi raktár, logisztikai bemutató |
| G40 | Középen, a bal gyártási tömbtől jobbra | Szerszámgyár |
| G42 | A G40 fölött | Energiaközpont |
| G50 | A nagy jobb oldali alsó tömb | Karosszériaüzem |
| G55 | A G50 alsó oldalán | Présüzem |
| G60 | A G50 fölött, a jobb tömb bal oldalán | Lakkozó |
| G70, G80 | A G60-tól jobbra, hosszú felső tömb | Jármű-összeszereléshez kapcsolódó bemutatók; G80-ban készautó-végellenőrzés |
| G86, G89 | A jobb tömb fölötti elkülönülő terület | Minőségvizsgálati bemutató, szélcsatorna |
| PTC | A fő tömbök alatt, középen | Projekt- és oktatóközpont |

A táblázat irányai a **forrás ábrájának tájolását** követik. A PDF ezen lapján nincs ellenőrzött északjel vagy méretarány, ezért ezek nem földmérési koordináták. A fekete vonal és a buszpiktogramok a nyílt napi közlekedést ábrázolják; ezekből nem következik a kész autók tényleges útvonala.

## További elsődleges források

**[2] AUDI HUNGARIA Zrt., „Audi Hungaria Nyílt nap”, 2023. június 28.** [Hivatalos eseményoldal](https://audi.hu/hu/hirek/legfrissebb-hireink/audi-hungaria-nyilt-nap.html). Erről az oldalról származik a PDF letöltési hivatkozása és a vállalat által linkelt [2023-as Google My Maps eseménytérkép](https://www.google.com/maps/d/viewer?mid=1xu9TdjBKCrbg7VKqYecn_VTF9uUu7J0&ll=47.69393450257183%2C17.693971724340514&z=15). Az eseményoldal gyárkapuk melletti látogatói parkolókat és központi buszparkolót is megnevez. Ez nem készautó-parkolási üzemi szabályzat.

**[3] AUDI AG, „Audi at the Győr site / Profile of location”, 2026. március 17.** [Hivatalos telephelyportré](https://www.audi.com/en/audi-at-the-hungary-site-gyor-5570/profile-of-location-5571). Megerősíti a hajtásgyártás, járműgyártás, fejlesztés, szerszámgyártás és logisztika különálló funkcióit. Az AGV-k csarnokon belüli alkatrészellátására és lézerszkenneres tájékozódására is kitér. Ez az állítás nem igazolja, hogy a kész autók önvezetően közlekednek a győri telephely udvarán.

## Mit vesz át a saját térkép?

A saját rajz megtartja a két nagy gyártási térséget, a köztük húzódó kapcsolatot, a jobb oldali járműgyári épületek sorrendjét és a G70-G80 tömb elnyújtott alakját. A tömbök összevonása, az apró kiszolgálóépületek elhagyása és a kiválasztott készautó-logisztikai terület kiemelése tudatos egyszerűsítés. A teljes üzemi folyamatból csak a gyártósor utáni puffer, a parkolóhely-foglalás és az oda vezető közlekedés kerül szimulálásra.

A parkolóblokkok pozíciója és mérete, az utak iránya, az átkelők, a közlekedési konfliktuszónák és az indulási pontok a szimuláció saját konfigurációjának részei. A kijelzett méterek ezen oktatási tér méretei. A blokkok relatív elhelyezése forrásalapú, de a modellből nem számolható vissza a valós gyár úthossza, parkolókapacitása vagy termelési teljesítménye.

Javasolt ábrafelirat a dokumentációban: **„A győri Audi-telephely nyilvános elrendezése alapján készített egyszerűsített oktatási szimuláció. Forrás: saját szerkesztés az Audi Hungaria 2023-as nyílt napi programfüzetének 2. oldala alapján [1]; az utak, parkolók és közlekedési paraméterek modellfeltevések.”**

## Továbbfejlesztési irányok

1. **Elrendezés és kapacitás összehasonlítása.** Ugyanazzal a véletlenmaggal több parkolókapacitás, gyártási érkezési ütem és kiindulási pont vizsgálható. A teljesülési arány mellett a puffer hossza, a beparkolási idő eloszlása és a megtett út mérhető.
2. **Forgalmi szűkületek elkülönítése.** Több konfliktuszóna és zebrák esetén a zónafoglalásra, gyalogosra és előző járműre várás külön mérőszámot kapjon. Így megállapítható, hogy a többlet parkoló vagy a megváltoztatott útvonal segít-e.
3. **Készautó-kiszállítás későbbi modellezése.** A parkolóhelyet elhagyó jármű útvonala és kiszállítási célja külön fázis lehet. Ez új forgalmi konfliktusokat hoz, ezért önálló verifikációt igényel.
4. **Érzékelési és kommunikációs bizonytalanság.** A meglévő hibainjektálást késleltetés-, adatvesztés- és zajszint-kísérletek egészíthetik ki. Az eredmény kizárólag a megadott szimulációs feltételekre vonatkozzon.

A hivatkozások hozzáférési dátuma egységesen: 2026-10-04. Az ellenőrzési PDF és a renderelt térképlap kutatási segédanyag; a beadandóba saját rajz, forráshivatkozás és a szükséges modellkonfiguráció kerüljön.
