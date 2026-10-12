# V0.8 – adat-, geometria- és felületkonzisztencia

A fejlesztés célja a kijelzett, exportált és ellenőrzött rendszerállapot összhangja. A rendszerhatár továbbra is a laptopon futó folyamatmodell, a tényleges MQTT-közvetítő, a koordinátor és a megjelenítő. A virtuális szenzorok nem fizikai Audi-eszközök; a telephely geometriája oktatási feltevéseket tartalmaz.

## Feltárt eltérések és javításuk

| Eltérés | Javítás | Mérnöki jelentőség |
|---|---|---|
| Frissesség ellenőrzésekor a nulla időbélyeg összekeveredett a minta hiányával. Egy korai parkolóengedély mellett a még mintát nem kapott autó elindulhatott. | A vezérlés és az állapotkép ugyanazt a tényleges mintajelenlétet és korhatárt használja. A 0 s időpontú érvényes minta is kezelhető. | Mozgásengedélyhez valóban rendelkezésre álló megfigyelés szükséges. |
| A közelségmodell fizikai épület- és parkolóautó-felületeket érzékelt, az ütközésszámláló azonban ezeket nem ellenőrizte. | Ugyanazon statikus testek téglalapjait független referenciaellenőrzés vizsgálja. | A nulla ütközés állítása szélesebb, egyértelműen megadott testkészletre vonatkozik. |
| Az út részletei az első 5 m-en belüli parkolóhelyet írták ki, amely szomszédos hely is lehetett. | A helymeghatározás a ténylegesen közeli, kiosztott vagy legközelebbi helyet választja. | A visszajátszás helyazonosítója összhangban van a pozícióval. |
| A térképi érzékelőréteg nem mutatta az új lökhárítós közelségcsatornákat. | Közös élő és történeti rajzoló jeleníti meg a mentett szerelési pontokat, nyalábokat és találatokat; csatornatáblázat mutatja a távolságot és a célt. | A radarjel és a geometriai bizonyíték azonos mintára vezethető vissza. |
| A közelségi világkoordináták vetítése zajos irányt használt, miközben a nyaláb saját pontos referenciairánya is mentve volt. | Az új geometriai nézet a közelségminta saját referenciáját használja; a régi, közelségcsatorna nélküli archívumok korábbi irányértelmezése megmarad. | A közelségi nyaláb, találat és irány egymással konzisztens. |
| Húzás után a parkoló billentyűs kiválasztása is tiltva maradhatott; archivált autóra hatástalan követőgomb volt aktív. | A húzásvédelem csak a pointeres kattintásra vonatkozik; a fő térképi követés aktív járműhöz kötött. | A műveletek visszajelzése és akadálymentes kezelése következetes. |
| A magyarázó szöveg rögzített hatótávot és színküszöböket állított konfigurálható értékek mellett. | A feliratok az adott fogadott csomagból készülnek. | Egyedi vagy archivált konfigurációnál sem keverednek a paraméterek. |
| Az útexportból hiányzott a futás saját térképe és konfigurációja; a rögzített mintahatár összegzése későbbi sorszámot tartalmazhatott. | Az önálló JSON-export futáskörnyezetet is hordoz; az összegzés a kért rögzített mintaszakaszra vonatkozik. | Az eredmény önállóan értelmezhető és következetesen reprodukálható. |
| A teljes futás ZIP-exportja a memória és a globális zár alatt épített fel nagyméretű CSV-ket. | Kötegelt olvasás, lemezre átforduló ideiglenes ZIP és darabolt HTTP-küldés, önálló adatbázis-olvasóval. | Az export mérete nem ugyanekkora memóriafoglalást jelent; a megfigyelés és a kezelői parancsok folytatódhatnak. |
| Egy elutasított sebességkérés mégis módosíthatta az újraindításkor használt sebességet. | A megjegyzett sebesség a szimulátor elfogadott állapotából frissül. | A parancsszándék elkülönül a végrehajtási bizonyítéktól. |

## Szenzorgeometria és adatút

A közelségcsatornákból rajzolt szerelési pont, nyalábvég és első találat az MQTT-n fogadott `sensors.proximity` csomag mentett világkoordinátáiból származik. Elavult csomag esetén a geometria és az eredeti mintavételi idő megmarad, a megjelenítés nem jelöl friss, szabad állapotot. Üres csatorna esetén nincs feltételezett akadály. A csatornatáblázat adat, ezért szövegesen kijelölhető.

A statikus ütközésvizsgálat a mintavételtől független modellreferencia-ellenőrzés. Eredménye külön eseményben tartalmazza az akadály azonosítóját, típusát és geometriai forrását. A számláló egy futásban egyszer számol egy érintett testpárt; nem az ismételt érintkezési epizódok száma. A nulla felületi távolság pontos érintkezést is jelenthet; ütközést a téglalapok tényleges átfedése igazol.

## Igazolási érvelés – megfigyelhetőség és reprodukálhatóság

| Követelmény | Megvalósítás | Bizonyíték |
|---|---|---|
| Szenzorminta nélkül a jármű nem gyorsíthat. | Közös mintajelenlét- és frissességvizsgálat a vezérlésben és a kijelzésben. | Korai érzékelőhiba + érvényes parkolóengedély regressziós tesztje; 0 s-os mintavételi határeset. |
| Egy geometriai találat ugyanott látszódjon a térképen és a mintanézetben. | Mentett koordináták és a közelségmodell saját referenciairánya, közös élő/történeti rajzoló. | Rendererellenőrzések, tényleges böngészős kijelölés és visszajátszás. |
| Az export a kért futás rögzített bizonyítékát tartalmazza. | Önálló olvasó, rögzített felső rekordhatár, konfiguráció és metaadat, pontos mentett szenzorcsomagok. | Prefix-, export- és visszaállítási tesztek; tényleges MQTT + HTTP export összevetése. |
| Az export közben az adatút kezelhető maradjon. | Kötegelt adatolvasás, globális alkalmazászár nélküli tömörítés és HTTP-küldés. | Párhuzamos állapot-/parancskezelés és erőforrás-korlát regressziós tesztjei. |

## Ellenőrzési eredmények

A végrehajtott ellenőrzések eredményét a `results/ELLENORZES.json`, a tényleges futtatási számokat a `results/unit-summary.json` tartalmazza. A négy alternatív győri elrendezés 30 járműves ellenőrzése önállóan újrafuttatható: `python -m experiments.verify_yard_layouts`. Ez determinisztikus protokollszintű modellteszt; a tényleges MQTT-adatutat külön a `python -m tests.perception_mqtt_smoke` ellenőrzi. A korábbi változatok mérései az eredeti dátummal és hatókörrel maradnak meg.

## Fennmaradó modellhatárok

A kormányzás továbbra is ideális töröttvonal-követés, a kiadás kifelé vezető út nélkül történik. A rövid közelségmodell véges számú sugarat és téglalaptesteket használ; nem szimulál ultrahang-terjedést, visszaverődési minőséget vagy fizikai gyalogostestet. A nagyobb környezeti minta takarást nem kezel. A szintetikus nézet geometriai rekonstrukció, nem kamerafelvétel. A rögzített futások lemezhelyigénye az adatmegőrzéssel növekszik; a nyers MQTT-napló körkörös méretkorlátja ezt nem korlátozza.
