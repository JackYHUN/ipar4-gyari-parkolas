# V07: irányonkénti közelségjelzés és egységes útvonalnézet

A parkolószenzor jellegű nézet a jármű körül mutatja az akadályok közelségét. A színes szektorok a szimulátorban mintavételezett felülettávolságokból készülnek, és ugyanazok a megfigyelések az élő nézetben, az archív útvonalon és az exportban is visszakereshetők. A megjelenítés az autók parkolássegítőinek működési elvét szemlélteti, saját mérnöki paraméterekkel.

Az Audi hivatalos Q8 e-tron műszaki ismertetője első, hátsó és oldalsó ultrahangos érzékelőket, valamint külön TopView kamerákat tárgyal [1]. Ez a projekt ezek szerepét egyszerűsített virtuális jelekkel szemlélteti; nem Audi hardverspecifikáció, és nem konkrét győri üzemi megvalósítás. A színhatárok és a szenzorelrendezés a saját modell feltevései.

## Nyolc virtuális érzékelő

Az első és hátsó lökhárítón három-három érzékelő, a két oldalon egy-egy érzékelő szerepel. A csatornák saját helyet és irányt kapnak a jármű koordinátarendszerében. A pozitív irányszög előrefelé nézve jobbra fordulást jelent.

| Tulajdonság | Modell és értelmezés |
| --- | --- |
| Hatótáv | Alapérték 3 m az érzékelő eredetétől mérve. |
| Sugárnyaláb | Csatornánként kilenc irány, ±20°-os tartományban. A véges nyaláb nem folytonos lefedettség. |
| Akadálygeometria | Forgatott járműtéglalapok és a modellben megadott fizikai épülettéglalapok. A jármű hosszát és szélességét a konfiguráció adja. |
| Mért távolság | A legközelebbi eltalált felület távolsága. Nem a másik autó középpontjának távolsága. |
| Közeli jelzés | Legfeljebb 0,55 m: piros. |
| Figyelmeztető jelzés | 0,55 m felett, legfeljebb 1,2 m: sárga. |
| Távolabbi találat | 1,2 m felett a hatótávon belül: zöld. |
| Nincs visszatérés | `distance_m=null`, `status=no_return`: szürke, inaktív csatorna. Ez nem bizonyítja a teljes környezet akadálymentességét. |
| Elavult minta | A régi távolság és találat megmarad; a nézet tompított, egységes színnel és elavult jelzéssel jelenik meg. Nem friss zöld állapot. |

A sugár az első eltalált téglalapnál véget ér. Ez a rövid hatótávú csatorna geometriai takarását modellezi, de nincs ultrahang-visszaverődés, anyagfüggés, interferencia, időjárás vagy érzékelési zaj. A CARLA hivatalos szenzorleírása is használ sugármetszésen alapuló szimulált érzékelést [2]; a jelenlegi megoldás saját kétdimenziós számítás, CARLA telepítése nélkül.

A kezdetben foglalt parkolóhelyeket külön címkézett álló járműgeometria helyettesíti, a konfigurált járműméretekkel és a parkolóút végének irányával. A zebra térképi jele önmagában nem fizikai akadály. A gyalogosokhoz a rendszer nem talál ki testgeometriát vagy pozíciót ebből a jelből.

A teljes konfiguráció `vehicle.parking_sensors` részében a `range_m`, `close_m`, `warning_m`, `beam_half_angle_deg` és `rays_per_channel` paraméterek felülírhatók. A színhatároknak a hatótávon belül szigorúan növekvőnek kell lenniük; a sugárszám 3–33 közötti páratlan érték lehet. A mikrométeres numerikus kerekítés a számítás és export felbontása, nem valós érzékelő pontossági állítása. A csatornaállapot ugyanebből a kerekített, archivált távolságból készül.

## Adatút és reprodukálhatóság

A teljes csomag a `sensors.proximity` mezőben, `virtual_bumper_ray_fan` forrásmegjelöléssel szerepel. Minden csatornához az érzékelő eredete, iránya, hatótávja, távolsága, állapota, a találat tárgya és helye, illetve a nyaláb végpontjai tartoznak. A csomag saját `sample_sim_time_s` ideje az eredeti érzékelőmintáé. Az archívum a teljes kapott csomagot megőrzi.

Érzékelőhiba alatt a nyaláb és a találat nem számolódik újra. A korábbi észlelés az eredeti helyén és időbélyegével marad; a döntési idő és az adatkor ettől külön változhat. A rövid hatótávú új érzékelés diagnosztikai jel: a meglévő fékezési és engedélyezési szabályok továbbra is a korábbi, külön mintavételezett adatút alapján működnek.

Az élő állókép és JSON az MQTT-állapotkép fogadási UTC-idejét és `snapshot` forrását is tartalmazza. A történeti letöltés az archivált telemetria vagy modelldiagnosztika saját fogadási idejét őrzi meg. Az állapotkép és az egyedi telemetriaüzenet külön fogadás; az időbélyegeik nem cserélhetők fel. Az érzékelési és döntési idő szimulációs másodperc, két tizedesjeggyel kijelezve.

## Megjelenítés és a mentett térkép

A közelségnézet a nyolc csatornát autó körüli szektorokként, felülettávolsággal mutatja. Régi, ilyen csatorna nélkül mentett futásból nem keletkezik utólag parkolószenzor-mérés; ilyenkor a felület a korábbi környezeti megfigyelést címkézi. A szintetikus elölnézet a mentett objektumlistából rajzol részletesebb perspektivikus járműveket és térképi zebraállapotokat, a járművek mintavételkor mentett irányával és méreteivel. A rövid hatótávú első felületi találatok külön jelként jelennek meg, a mentett találati koordináták alapján. Hiányzó vagy régi csomagból nem keletkezik új felületi találat. A grafika továbbra sem kamera, és a hosszabb, 18 m-es környezetlista takarás nélküli modellmegfigyelés marad.

Az élő és az útvonal-visszajátszási térkép közös rajzolót használ. A csarnokok, útfelületek, zebrák, parkolócellák, járműgrafika és feliratstílus azonos. A történeti nézet a kiválasztott futás mentett konfigurációját használja; nem az élő futás geometriáját. A teljes parkolóállapot nincs minden járműmintához archiválva, ezért a történeti parkolóhelyek semleges megjelenítést kapnak, a kiválasztott hely külön kiemeléssel. Más autó csak az adott mintában megőrzött megfigyelésként szerepelhet.

A történeti térkép saját nagyítási, húzási, forgalmi területre illesztési és autókövetési vezérlést kapott. A visszajátszási időcsúszka a mentett útpozíciót mozgatja; az érzékelőnyaláb külön, az eredeti mintavételi helyén jelenik meg. Így egy kiesés alatt a korábbi észlelés nem tapad tévesen az autó új helyzetéhez. A visszajátszás nem használja az élő futás más autóinak vagy parkolóérzékelőinek állapotát.

## Igazolási szempont

| Követelmény | Megvalósítás | Ellenőrzés |
| --- | --- | --- |
| A közelség valódi modellbeli felülettávolság legyen. | Sugármetszés forgatott testtéglalapokkal, az első találat kiválasztása. | Ismert távolságú és különböző irányú testek, takarás és nincs-találat tesztesetek. |
| Elavult mérés ne jelenjen meg új észlelésként. | Csak sikeres mintavételkor keletkező nyalábcsomag, hibakor változatlan tárolt megfigyelés. | Hibainjektálás és az eredeti csomag/időbélyeg egyezése. |
| A visszajátszás ne keverje az élő és történeti világot. | Közös rajzoló külön mentett térképi bemenettel, semleges ismeretlen állapotokkal. | Más futás betöltése, időcsúszka, nagyítás és az előzmény felületi ellenőrzése. |
| Az új diagnosztika ne módosítsa a korábbi vezérlést. | Determinisztikus, véletlenszámot és fizikai állapotot nem módosító megfigyelő. | 500 modelllépésnél a mozgás, protokollüzenetek, események és véletlenszám-generátorok összevetése az új megfigyelő nélkül. |

A konkrét teszteredmények a `results/ELLENORZES.json`, `results/unit-summary.json` és `results/PERCEPTION_MQTT_RENDSZERTESZT.json` fájlokban találhatók. A színkijelzés geometriai mérés és megjelenítés ellenőrzése; nem igazolja valós jármű parkolási biztonságát.

A V07 tényleges MQTT/HTTP-vizsgálatában két autó beparkolt, nulla modellbeli ütközéssel. A CAR001 archívuma 2486 fogadott keretet tartalmazott, köztük 60 hibakori diagnosztikai keretet; a külön érzékelőminták száma 2426 volt. Nyolc közelségcsatorna, a saját mintavételi idő és a hibakor változatlan teljes csomag ellenőrzése megtörtént. A lapozott előzmény és CSV/JSON-export a nyers napló rotációja, valamint új futás indítása után is egyezett. A keretszám a fogadott archívumra vonatkozik, nem a veszteségmentes MQTT-kézbesítés állítása.

A `tests/test_proximity.py` tíz célzott esetben ellenőrzi a felülettávolságot, az elfordulást, a takarást, a határértékeket, a foglalt helyek és épületek geometriáját, az érzékelőhibát és a vezérléstől való függetlenséget. A `node tests/perception_visuals_smoke.cjs` kilenc rajzolási ellenőrzést végez, beleértve a régi csomagokat, a színhatárokat, az elavult jelzést, a mentett irány és találati hely felhasználását, valamint a véges geometriájú, passzív SVG-exportot. A böngészős ellenőrzések és a letöltött keretek bizonyítékai a `results/ui/` könyvtárban vannak.

A teljes egységtesztcsomag 151 sikeres tesztet adott. A beépített böngészőből letöltött hibás érzékelőkeret JSON- és SVG-metaadata megegyezett az archivált MQTT-mintával: az érzékelés ideje 0,25 s, a döntésé 3,05 s. A reprodukálható egyezésvizsgálatot az `experiments/check_frame_download.py`, az eredményt a `results/ui/proximity-download-v07-check.json` őrzi. A hibás HTTP-exportkérések kezelése korlátozott törzsürítéssel javult; a formátum- és méretellenőrzések továbbra is érvényesek.

## A fizikai valósághűség határa

A közelségérzékelő pontosan a megadott kétdimenziós testgeometriát vizsgálja. A kilenc sugár közötti rések, az ismeretlen gyalogostest, valamint az ultrahang visszaverődése és interferenciája továbbra is modellkorlát. A távolsági szín és a perspektivikus rajz önmagában nem alkalmas valós szenzor pontosságának vagy észlelési valószínűségének igazolására.

A fizikai manőver valósághűségéhez külön lépés a kormányzott járműdinamika, a tolatással végrehajtott parkolás és az érzékelésen alapuló pályakövetés bevezetése. A szenzoroldalon a gyalogos testgeometriája és a mérési hibamodell igényelne következő verifikációs kampányt. Ezek a jelenlegi változatban még nincsenek megvalósítva; a V07 a felülettávolság, az adateredet és a történeti megjelenítés ellenőrizhetőségét javítja.

## Irodalom

[1] AUDI AG, “Prepared for any everyday situation: Assistance Systems and Digitalization,” *Audi Q8 e-tron Basic Information*, Audi Communications, 2023. [Online]. Elérhető: [Audi hivatalos műszaki ismertető](https://uploads.audi-mediacenter.com/system/production/uploaded_files/23842/file/18e6974a6e8c14882e19417adf9571c72977ad8b/230512_Basisinfo_Audi_Q8_e-tron_EN.pdf). Hozzáférés: 2026. okt. 5.

[2] CARLA Team, “Sensors reference,” *CARLA Simulator Documentation*, frissülő dokumentáció. [Online]. Elérhető: [CARLA szenzorleírás](https://carla.readthedocs.io/en/latest/ref_sensors/). Hozzáférés: 2026. okt. 5.
