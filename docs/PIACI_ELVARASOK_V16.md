# Piaci elvárások és a prototípus V16 fejlesztése

Forrásellenőrzés: 2026. október 11. A következő összevetés nyilvános gyártói és szabványgazdai anyagokat használ. A követelmények kiválasztása mérnöki értelmezés; az anyagok nem a hallgatói rendszer elfogadási feltételei.

| Nyilvános elvárás | Indoklás | Megvalósítás ebben a változatban |
| --- | --- | --- |
| Közös flotta- és rendszerdiagnosztika | A SIMOVE valós idejű járműinformációt, navigációs diagnosztikát és rendszerjelzéseket ismertet [1]. | Közös üzemeltetési panel, súlyosság, érintett jármű és vizsgálati javaslat. |
| A kapcsolat és a jármű állapota külön értelmezendő | A VDA 5050 külön kommunikációs és hibainformációt ír le [2]. | Külön szabály kommunikációhiányra, friss érkezés melletti feldolgozási lemaradásra és virtuális érzékelőhibára. |
| A kezelői észlelés elkülönül a hiba megszűnésétől | Az OPC Foundation külön nyugtázási állapotot és auditálható nyugtázási műveletet definiál [3], [4]. | Önálló aktív és nyugtázott állapot, tartós megjegyzés, egyértelmű epizódazonosító, ismételt hiba esetén új epizód. |
| Visszakereshető bizonyíték | A gyártói diagnosztika és hibareferenciák gyakorlati célja a hiba vizsgálata [1], [2]. | Az epizód keletkezésekor rögzített forgalmi mintából nyitható meg az addig fogadott jármű- és eseményelőzmény. |

A gyári autómozgatás alkalmazási körét a Bosch Automated Vehicle Maneuvering nyilvános leírása is alátámasztja: készjárművek mozgatása gyártás, vizsgálat és logisztikai terület között [5]. Ez a forrás nem igazolja a győri telephely konkrét belső útvonalait. A térkép továbbra is nyilvános információk alapján készült, egyszerűsített oktatási modell.

A V16 saját MQTT- és HTTP-adatmodellre épül. A piaci elvek átvétele nem jelent VDA 5050 vagy OPC UA implementációt, ipari megfelelőséget vagy fizikai biztonsági tanúsítást. Az idő- és ismétlésszám-küszöbök a prototípus kimondott mérnöki választásai. Nem gyártói előírások vagy garantált reakcióidők. Az alkalmazott szabályokat, megőrzést és a tényleges ellenőrzési eredményeket az [üzemeltetési leírás](UZEMELTETES_V16.md) tartalmazza.

Későbbi, külön méretezendő lehetőség a VDA 5050 adapter, a küldetések prioritásos kiosztása és az MES-rendelésekkel való összekapcsolás. Ezekhez önálló protokoll- és modellvizsgálat kell; a jelen változat a diagnosztikai és kezelői feladatot valósítja meg.

## Irodalom

[1] Siemens AG, „SIMOVE – Automated Guided Vehicle systems”, Siemens termékismertető. [Online]. Elérhető: https://www.siemens.com/en-us/products/simove/. Hozzáférés: 2026. okt. 11.

[2] Verband der Automobilindustrie és VDMA, „VDA 5050 – Interface for the communication between mobile robots and a master control”, 3.0.0 kiadás, 2026. márc. [Online]. Elérhető: https://github.com/VDA5050/VDA5050/blob/main/VDA5050_EN.md. Hozzáférés: 2026. okt. 11.

[3] OPC Foundation, „OPC Unified Architecture – Part 9: Alarms & Conditions, 4.3 Acknowledgeable Conditions”, 1.05.06 kiadás. [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/4.3. Hozzáférés: 2026. okt. 11.

[4] OPC Foundation, „OPC Unified Architecture – Part 9: Alarms & Conditions, 5.7.3 Acknowledge Method”, 1.05.06 kiadás. [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/5.7. Hozzáférés: 2026. okt. 11.

[5] Robert Bosch GmbH, „Fahrerlose Fahrzeuge: Bosch bringt mit Automated Vehicle Maneuvering mehr Effizienz ins Werk”, sajtóközlemény, 2023. ápr. 25. [Online]. Elérhető: https://www.bosch-presse.de/pressportal/de/de/fahrerlose-fahrzeuge-bosch-bringt-mit-automated-vehicle-maneuvering-mehr-effizienz-ins-werk-253766.html. Hozzáférés: 2026. okt. 11.
