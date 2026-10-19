# Témaválasztási adatlap

[Újra kitölthető Word-sablon](sablon/temavalaszto-adatlap.docx)

A sablon a korábban használt tantárgyi adatlap szerkezetéből készített, személyes adatok nélküli változat. A szürke helyőrzőket kitöltéskor saját adatokkal és szöveggel kell helyettesíteni. A kész adatlapot PDF-ként kell leadni. Az alábbi törzsszöveg 1795 leütés szóközökkel, sorszámokkal és bekezdésváltásokkal; a cím ezen felül értendő.

## A téma címe

Elkészült járművek gyári mozgatásának és parkolásának szimulációja MQTT-alapú helyfoglalással és gyalogosészlelésre épülő megállítással

## A téma kifejtésének fontosabb szempontjai

A feladat célja az elkészült járművek gyártósori kijárattól parkolóhelyig történő mozgatásának vizsgálata egy laptopon futó, kétdimenziós gyárudvarmodellben. A hat aktív autót, két zebrát és tizenhat parkolóhelyet tartalmazó referenciamodell központi helyfoglalást, valós MQTT-adatutat és virtuális érzékelésre épülő helyi megállítást alkalmaz.

1. A rendszerhatár és a mérnöki feltételezések rögzítése, a fizikai, logikai, jelátalakító és emberi CPS-komponensek besorolása. A szerkezet és a működés UML- vagy SysML-diagramokon kerül bemutatásra.

2. A gyártósor készre jelentése után helyet kérő járművek, valamint a szabad, lefoglalt és foglalt parkolóhelyek modellezése. A helyzetadatok és vezérlési üzenetek publikáló-közvetítő-feliratkozó láncon haladnak. Az adatok mértékegysége és mintavételi gyakorisága rögzítésre kerül.

3. A gyalogosok és akadályok virtuális érzékelésének megvalósítása. A helyi vezérlés a sebesség, a fékezési korlát és az érzékelési késés alapján állítja meg az autót; működését nem kizárólag távoli parancs biztosítja.

4. A rögzített helyszámsorrend és a legrövidebb elérhető út alapján történő parkolókiosztás összehasonlítása. Több véletlenmaggal, azonos eseménysorok mellett ismételt mérések készülnek a parkolásig eltelt időről és a megtett útról.

5. Az egyidejű helyigénylés, a megtelt parkoló, a gyalogos áthaladása és a kommunikációs hibák tesztelése. Egy választott szempont, a foglalások következetessége követelmény-megvalósítás-bizonyíték érveléssel kerül végigvezetésre.

Az eredmény működő, naplózott szimuláció, böngészős műszerfal és a két kiosztási módszer statisztikai értékelése lesz. A forráskód, a konfiguráció, az indítási útmutató és a bemutatóvideó a reprodukálhatóságot támogatja; a következtetések a kimondott modellfeltételekre korlátozódnak.
