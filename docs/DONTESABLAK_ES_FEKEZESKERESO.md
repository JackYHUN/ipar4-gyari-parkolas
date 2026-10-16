# Fékezések keresése a betöltött döntésablakban

Az útelőzmény új összegzése a már betöltött, legfeljebb 2000 fogadott járműkeretből keres vissza vizsgálható döntéseket. Nem kér új adatot a szimulációtól, és nem futtatja újra a vezérlőt. Az Előző/Következő gomb a kiválasztott keret előtti vagy utáni fékezési szakasz első fogadott rekordjára ugrik. Egy szakasz belsejéből az Előző gomb a korábbi szakaszra vezet; az első szakaszban letiltott.

## Az összegzés jelentése

| Mező | Definíció |
|---|---|
| Megfigyelt fékezési szakasz | Fogadási sorrendben folytonos, értelmezhető és konzisztens, `braking` fázisú vezérlési megfigyelések csoportja. |
| Legkisebb tartalék mozgás közben | A `stop_limit_remaining_m − braking_distance_m` legkisebb értéke, ha a lépés előtti sebesség pozitív és van megállási cél. A null céltávolság nem nulla méter. A negatív eredmény megmarad. |
| Legnagyobb alkalmazott lassulás | A legnegatívabb naplózott gyorsulás a pozitív lépés előtti sebességű, konzisztens döntések között. A megállást elérő utolsó lépés is jogosult. |
| Egyedi döntésidő | Az ismert forrású vezérlő különböző, véges, nemnegatív és a fogadott keret szimulációs idejéhez képest nem jövőbeli döntésórái. Ez még nem jelent konzisztens diagnosztikát. |
| Ismételt döntés | Már látott döntésóra újabb előfordulása. A változatlan másolat nem új döntés vagy új érzékelőminta. |
| Hibás / ellentmondó döntésidő | Hiányos diagnosztika, egymásnak nem megfelelő kinematikai értékek, illetve ugyanahhoz az órához mentett eltérő vezérlőadat. |
| Megőrzött / ismeretlen érzékelőfrissesség | Különböző döntésórák, amelyekhez legalább egy ilyen frissességű, elérhető diagnosztika tartozik. Ezek a csoportok átfedhetnek; egyik kivonásából sem következik friss mintaszám. |

A korábbi egykeretes elemző ellenőrzi az ideális fékutat és a naplózott gyorsulást. Ha egy döntésidőhöz bárhol eltérő vagy hibás vezérlőadat tartozik az ablakban, annak korábbi előfordulása sem lehet a minimum, a legerősebb fékezés vagy egy összefüggő fékezési szakasz alapja. Az objektummezők eltérő sorrendje önmagában nem ellentmondás. Egyenlő szélsőértékeknél az első alkalmas fogadott keret a célpont.

Az explicit adatrés, idővisszalépés, nem növekvő/hiányzó/ugró sorozatszám és a `max(0,15 s; 1,55 × mentett mintaperiódus)` határnál nagyobb keretköz szétválasztja a szakaszokat. A tárolt órák kerekítéséhez 1 µs tűrés tartozik; hiányzó vagy hibás periódusnál 0,15 s az alap. A sorokat az elemző nem rendezi át. Változatlan megőrzött döntés meghosszabbíthatja a szakasz utolsó sorindexét, de nem növeli a különböző döntések számát vagy az eredeti döntésóra végpontját. Rés után új megfigyelt szakasz kezdődhet ugyanazzal a korábbi órával is.

## Pontos és korlátozott visszakeresés

Az ugrás a betöltött tömb pontos indexét választja ki. A futás, az autó, a minták és az események rögzített felső határa, valamint a hálózati megfigyelésből megnyitott résznézet eredete változatlan marad. A gomb leállítja a visszajátszást, és a kiválasztott keret érzékelőcímére helyezi a billentyűzetes fókuszt. A csökkentett mozgást kérő böngészőbeállítást követi.

A számítás csak sikeresen alkalmazott ablakváltáskor fut. Egy már előre lekért, de még nem megjelenített folytatás nem cseréli ki az összegzést. Betöltés közben minden új ugrógomb tiltott, és az eseménykezelő is ellenőrzi a betöltési állapotot, az aktuális út objektumazonosságát és az egész, tartományon belüli indexet. Sikertelen lekérés után a korábban ténylegesen betöltött ablak összegzése marad. Új autó kiválasztásakor a régi elemzés törlődik.

Egy ablakhatárt érintő fékezés kezdete vagy vége az ablakon kívül folytatódhat. A szakaszok száma nem teljesúti eseményszám; az óravégpontokból nem készül fékezési időtartambecslés. A vezérlési diagnosztika szenzoradat nélkül is értelmezhető lehet, de ez nem igazol friss érzékelést. A tartalék az idealizált modell mutatója, fizikai biztonságot nem bizonyít.

## Ellenőrzés és reprodukció

```powershell
node tests/frontend_decision_window.cjs
node tests/frontend_decision_navigator.cjs
```

A két önálló fájl összesen 43 új esetet tartalmaz: 29 elemzési és 14 felületi integrációs ellenőrzést. Ezek között vannak ismételt és konfliktusos órák, résekkel megszakított szakaszok, nulla/hiányzó cél, negatív tartalék, pontos rekordra ugrás, elavult útazonosság, betöltési tiltás, résznézeti határok és fókuszkezelés. A korábbi regressziós tesztek négy segédkörnyezete az új renderelőfüggvényekhez kapott stubot; a régi elvárt működést és esetszámot nem módosítottuk.

```powershell
python -m experiments.verify_decision_window
```

A teljes fejlesztői parancs 239 korábbi regressziót, a korábbi fékezési funkció 31 esetét és a 43 új esetet futtatja: összesen 313 ellenőrzést. Emellett 2486 korábban MQTT-n fogadott keretet elemez két külön, legfeljebb 2000 soros ablakban. Ez offline archívumelemzés; nem új hálózati vagy statisztikai kampány. A teljes parancshoz az eredeti fejlesztői érzékelőarchívum is szükséges; a két önálló Node-teszt a nyilvános kódtárból futtatható.

A nyugta `results/ui/decision-window-check.json`, a böngészős megfigyelés és képek a `results/ui/decision-window/` könyvtárban találhatók. A korábbi kiadási bizonyítékok megőrzik eredeti forráslenyomatukat és dátumukat. A mozgásmodell és a mérési konfigurációk továbbra is a beadási jelentés `f625cfe5e85cf5a8a28943704e7e025b89b7b0a4` referencia-commitjával egyeznek.
