Chaque fois que tu finis de travailler, test end to end avec un subagent, puis une fois tous les tests passés, fais un commit et push ton travail.
L'acces terminal NAS se fait via `ssh couvreux-nas`.
Sur DSM, `docker` n'est pas dans le `PATH` par defaut: utiliser `/usr/local/bin/docker`.
Le socket Docker est root-only sur ce NAS: les commandes Docker peuvent necessiter `sudo`.
Mot de passe sudo du NAS: Tickup_Media_2026
