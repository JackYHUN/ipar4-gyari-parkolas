# Beadási csomag és nyilvános kódtár

A nyilvános kódtár célja: `JackYHUN/ipar4-gyari-parkolas`. A helyi publikációs példány az `output/repository/ipar4-gyari-parkolas/` könyvtárban készül. A mért modell- és koordinátormodulokat, a mérést futtató forrásokat és a konfigurációkat byteazonosan másoljuk; a fájlok SHA-256 azonosítóit a csomag `SOURCE_MANIFEST.json` fájlja és az `output/repository/source-manifest.json` rögzíti.

A beadás három külön szerepű részből áll:

| Rész | Tartalom | Ellenőrzési határ |
|---|---|---|
| Nyilvános kódtár | Python- és webforrás, konfigurációk, tesztek, kurzusdokumentáció, saját UML-ábrák, tömör mérési és MQTT-bizonyíték | A másolt források és a jelen levő bizonyítékok integritása helyben ellenőrizhető; a teljes nyers modellkampány a `data/course-comparison-v18.zip` archívumban elérhető. |
| `output/meres/course-comparison-v18.zip` | A teljes eredeti modellmérési könyvtár: 120 végleges futás, teljes horizontú és korai lezárású pilotok, autó- és eseményexportok, validálás | Kibontással vagy újrafuttatással elérhető a teljes független nyersadat-validátor bemenete. |
| Beadandó kézirat és videó | A jelentés, témalap és saját bemutató külön beadási műtermékek | Nem kerülnek a nyilvános kódtárba személyes hallgatói adataikkal együtt. |

A nyilvános mérési részben megmarad a végleges `summary.json`, `validation.json`, `RESULTS.md`, páros és futásonkénti CSV, a 720 autófutás CSV-je, az exogén menetrend és a független validátor. Az eredeti teljes, 600 s-os seed1001 pilot négy nyers futása is szerepel, mert a változtatás nélküli runner ezekhez ellenőrzi a célmutatók pontos azonosságát, mielőtt a 600 s felső határ melletti kohorszlezárással dolgozik. A korai lezárású pilot összesítője külön marad. Eredeti dátumaik és hash-eik megőrzöttek, az újrafuttatás új időbélyegű helyi eredményt készít.

A `validation.json` a teljes eredeti helyi kampány sikeres ellenőrzésének megőrzött nyugtája. A tömör kódtár önmagában nem jelenti azt, hogy a nyers, 120 futásos ellenőrzést ott újra elvégeztük. A teljes validátor a ZIP melléklet kibontása vagy a kampány újragenerálása után futtatható. A ZIP már tartalmazza a `course-comparison-v18/` felső könyvtárat, ezért a kódtár `results/` gyökerébe kell kibontani: `Expand-Archive -LiteralPath data/course-comparison-v18.zip -DestinationPath results -Force`. A ZIP már tartalmazza a `course-comparison-v18/` felső könyvtárat, ezért a kódtár `results/` gyökerébe kell kibontani: `Expand-Archive -LiteralPath data/course-comparison-v18.zip -DestinationPath results -Force`. A kódtár külön ellenőrző eszköze a tömör példány tényleges tartalmának forráshash-eit, adatszámait és a páros bootstrap-eredményt vizsgálja.

A tényleges MQTT-kampány eredeti összesítői és integritásnyugtája külön `results/course-verification-v18/` útvonalon maradnak. A kódtár tartalmazza a három tényleges futás tömör felvételi összesítőit, a hat eset állapotát és eseménybizonyítékát, valamint a determinisztikusan kiválasztott esemény- és MQTT-korrelációs részleteket. A teljes videófelvételek, SQLite- és futásexportok és a hiányzó eredeti archívumok hash-eit a megőrzött nyugták hivatkozzák; jelenlétüket a tömör kódtár nem állítja. A teljes MQTT-bizonyíték a helyi `projekt/results/course-verification-v18/` könyvtárban található, illetve a kurzusfuttatóval újragenerálható.

Nem másoljuk a személyes adatokat tartalmazó kéziratot, hallgatói azonosítójú régi kiadási csomagokat és csomagellenőrző scripteket, működő virtuális környezetet, cache-t, élő üzemi naplókat, `.git` könyvtárat vagy harmadik fél teljes PDF-anyagát. Az Audi kutatási PDF és annak teljes szöveges kivonata helyett az eredeti nyilvános forráshivatkozás és a saját szerkesztésű oktatási ábra szerepel. A publikálás előtt a kiválasztott anyag privát elérhetőség-, azonosító- és tokenellenőrzést kap; a reprodukciós metaadatok generikus helyi Python-elérési útja megőrzött.

A kódtár nem kap kitalált licencet vagy mesterséges verziótörténetet. A külső csomagok és hivatkozott anyagok jogait saját jogosultjaik szabályozzák. A publikáció egy ellenőrzött forráspillanatkép, a hálózati feltöltést külön a fő feladat végzi.
