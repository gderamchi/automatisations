# Manuel utilisateur - Automatisation documentaire

Derniere mise a jour : 12 mai 2026

Public : utilisateur client, non technique.

Objectif : comprendre comment envoyer, verifier, corriger et valider les documents traites par l'automatisation, de la reception par email jusqu'au classement, aux fichiers Excel et a InterFast.

> **A retenir :** l'automatisation fait le travail repetitif, mais elle garde un controle humain aux moments importants. Vous devez verifier les donnees lues par OCR et confirmer le bon chantier avant l'ecriture finale.

## Resume en une minute

- Vous envoyez les factures ou justificatifs a l'adresse email convenue.
- L'automatisation recupere les pieces jointes, lit le document avec OCR, extrait les informations utiles et prepare une proposition.
- Vous recevez une reponse automatique avec un lien de verification si une action est necessaire.
- Vous comparez le document original avec les champs detectes, puis vous corrigez si besoin.
- Vous confirmez le chantier, la nature du document et les choix Excel.
- Apres validation, le systeme classe le document, ecrit dans les classeurs Excel et envoie vers InterFast si ce mode est active.

## Chemins utiles sur le NAS

Chemins confirmes sur le NAS le 12 mai 2026.

Dans DSM / File Station, le dossier client a ouvrir est :

```text
Professionnel_CCM > 12_AUTOMATISATION > documents
```

Le chemin technique complet est :

```text
/volume1/Professionnel_CCM/12_AUTOMATISATION/documents
```

Si vous cherchez le document original recu par email ou depose manuellement, ouvrez :

```text
/volume1/Professionnel_CCM/12_AUTOMATISATION/documents/archive/originals/AAAA/MM/JJ
```

Exemple de logique : un document recu le 19 avril 2026 est range dans `archive/originals/2026/04/19`.

Le nom du fichier original est renomme par le systeme pour eviter les doublons :

```text
AAAAMMJJTHHMMSSZ_nom-du-fichier_hash.extension
```

Les autres dossiers utiles :

- `archive/normalized` : donnees OCR normalisees en JSON, utiles surtout pour l'administrateur.
- `classified/standard` : copie classee du document apres validation finale.
- `classified/accounting` : copie utilisee pour le suivi comptable.
- `classified/worksites/<chantier>` : copie rangee par chantier apres validation finale.

Quand les fichiers apparaissent :

- des la reception traitee, l'original est copie dans `archive/originals/AAAA/MM/JJ` ;
- apres **Confirmer et ecrire dans Excel**, les copies classees apparaissent dans `classified/*` ;
- si le document est rejete, l'original reste dans `archive/originals`, mais il n'y a normalement pas de copie classee ;
- si le document est un doublon exact, le systeme reutilise le document deja connu au lieu de creer une nouvelle copie originale.

> **Important :** les chemins qui commencent par `/data` sont les chemins internes Docker. Pour un utilisateur dans File Station, utilisez le chemin DSM `Professionnel_CCM > 12_AUTOMATISATION > documents`.

## 1. Ce que fait l'automatisation

L'automatisation sert a reduire le traitement manuel des documents comptables et chantier.

Elle peut :

- surveiller une boite email et recuperer les nouveaux messages avec pieces jointes ;
- archiver le document original dans `archive/originals/AAAA/MM/JJ` ;
- lire le contenu du document avec OCR ;
- detecter fournisseur, numero de facture, date, montants et reference chantier ;
- demander une validation humaine si une donnee est incertaine ;
- proposer un chantier et un classement ;
- ecrire les informations dans les classeurs Excel configures ;
- copier le document dans les bons dossiers NAS ;
- rattacher le document a une cible InterFast quand la configuration le permet ;
- afficher un tableau de bord avec les documents a traiter et les derniers traitements.

Elle ne remplace pas votre jugement. Si le montant, le fournisseur ou le chantier semble faux, corrigez avant de valider.

## 2. Ce qu'il faut avoir avant de commencer

Avant d'utiliser le systeme, assurez-vous d'avoir :

- l'adresse email de depot des documents ;
- le lien de l'interface ou du dashboard ;
- les identifiants du dashboard si un mot de passe est demande ;
- votre acces InterFast si vous devez verifier le resultat final ;
- la liste ou le nom des chantiers a utiliser ;
- les consignes internes sur les documents a accepter ou rejeter.

> **Important :** ne partagez pas les liens de validation dans des canaux publics. Un lien donne acces au document concerne.

## 3. Envoyer un document par email

Envoyez un email a l'adresse convenue avec une ou plusieurs pieces jointes.

Formats recommandes :

- PDF ;
- image lisible, par exemple JPG, PNG, TIFF ou WEBP ;
- fichier texte simple si c'est un cas prevu.

Evitez :

- les documents flous ou coupes ;
- les fichiers proteges par mot de passe ;
- les archives ZIP ;
- les captures d'ecran illisibles ;
- les fichiers Word ou Excel comme justificatif principal, sauf consigne specifique.

Bon reflexe : mettez une indication utile dans l'objet ou dans le corps du mail.

Exemple :

```text
chantier: Villa Martin
client: Martin
type: facture
fourniture: materiel
```

Ces lignes aident le systeme a proposer le bon chantier et la bonne categorie.

> **Note :** le worker traite les nouveaux emails non lus. Si un document n'est pas pris en compte, renvoyez-le dans un nouvel email ou demandez a l'administrateur de verifier la boite de reception.

## 4. Comprendre la reponse automatique

Quand le mail est traite, vous pouvez recevoir une reponse automatique. Le sujet commence generalement par `[AUTOMATISATIONS OCR]`.

La reponse liste les pieces jointes et leur statut.

Statuts courants :

- **classe automatiquement** : le systeme a pu traiter le document sans action de votre part ;
- **A VERIFIER** : une action humaine est necessaire ;
- **chantier a confirmer** : les donnees principales sont lues, mais le rattachement chantier doit etre confirme ;
- **erreur** : le fichier n'a pas pu etre traite ;
- **deja importe** : le fichier a deja ete vu par le systeme.

Si la reponse contient un lien `Verifier et valider vos documents`, ouvrez-le. Vous verrez les documents du mail et les actions a faire.

## 5. Utiliser la page "Vos documents"

La page "Vos documents" rassemble les documents d'un meme email.

Pour chaque document, regardez :

- le fournisseur ;
- le numero de facture si disponible ;
- le montant ;
- la date ;
- le chantier si le systeme l'a reconnu ;
- le statut.

Actions possibles :

- **Verifier ce document** : ouvre la validation OCR ;
- **Confirmer le chantier** : ouvre la validation de routage ;
- **Voir sur InterFast** : ouvre InterFast si le document a ete rattache.

Statuts utiles :

- **A verifier** : vous devez controler les informations lues ;
- **Chantier a confirmer** : vous devez confirmer le classement ;
- **Valide** : la validation est faite, mais le traitement final peut encore etre en cours ;
- **Classe** : le document est alle au bout du processus ;
- **Rejete** : le document a ete refuse.

## 6. Valider les donnees OCR

Sur la page de validation, l'ecran est separe en deux zones.

A gauche : l'aperçu du document original.

A droite : les informations detectees par OCR.

Controlez en priorite :

- le fournisseur ;
- le numero de facture ;
- la date de facture ;
- le montant HT ;
- la TVA ;
- le montant TTC ;
- la reference chantier.

Si une donnee est fausse, corrigez directement le champ avant de valider.

Boutons disponibles :

- **Valider** : confirme les donnees et passe a l'etape chantier/routage ;
- **Demander correction** : met le document de cote pour correction, sans dispatch final ;
- **Rejeter** : arrete le traitement de ce document.

> **Regle simple :** ne cliquez sur Valider que si le document visible et les champs affiches racontent la meme chose.

## 7. Confirmer le chantier et le routage

Apres validation OCR, l'automatisation propose comment classer le document.

Vous devez verifier :

- la nature du document, par exemple facture, devis, avoir ou recu ;
- le type de fourniture, par exemple materiel, carburant, hotel, repas, peage ou consommable ;
- le chantier selectionne ;
- le nom de depense ;
- les informations fournisseur, facture, date et montants ;
- les choix comptables et Excel proposes.

Le champ **Chantier** est important. Si le mauvais chantier est selectionne, choisissez le bon dans la liste avant de continuer.

La section **Previsualisation Excel** montre les ecritures qui seront envoyees dans les classeurs.

Etats possibles :

- **Pret** : le fichier cible et les valeurs sont disponibles ;
- **A choisir** : plusieurs possibilites existent, choisissez la bonne dans la liste ;
- **Manquant** : le systeme ne trouve pas le fichier ou la configuration necessaire.

Quand tout est correct, cliquez sur **Confirmer et ecrire dans Excel**.

Cette action peut declencher :

- l'ecriture dans les classeurs Excel ;
- la copie du document dans `classified/standard`, `classified/accounting` et `classified/worksites/<chantier>` ;
- le rattachement ou l'envoi vers InterFast selon le mode actif ;
- la mise a jour du dashboard.

## 8. Que se passe-t-il apres validation finale

Une fois le routage confirme, le systeme execute le dispatch.

Selon la configuration, il peut :

- copier le document dans un dossier standard ;
- copier le document dans un dossier comptable ;
- copier le document dans le dossier du chantier ;
- ecrire dans les fichiers de tresorerie, grand livre, chantier et TVA ;
- journaliser chaque tentative de dispatch ;
- envoyer ou rattacher le document dans InterFast ;
- envoyer une notification si Telegram est configure.

Si un fichier Excel bloque l'ecriture, la page peut afficher un message de type `Validation bloquee`. Dans ce cas, corrigez le choix du classeur ou signalez le blocage a la personne qui administre le systeme.

## 9. Utiliser le dashboard

Le dashboard est la vue de pilotage. Il peut demander un identifiant et un mot de passe.

Il affiche notamment :

- **Total documents** : nombre total de documents connus du systeme ;
- **En validation** : documents qui attendent une verification OCR ;
- **En routage** : documents qui attendent une confirmation chantier ;
- **Rejetes** : documents refuses ;
- **Exports en attente** : ecritures comptables pas encore exportees ;
- **Anomalies bancaires** : rapprochements bancaires a verifier ;
- **DOE incomplets** : dossiers chantier avec pieces manquantes.

Les deux listes les plus importantes sont :

- **Validations en attente** : a traiter quand une lecture OCR est incertaine ;
- **Routages en attente** : a traiter quand le classement chantier doit etre confirme.

## 10. Cas particuliers et quoi faire

### Aucun email automatique n'arrive

Attendez quelques minutes, puis verifiez :

- que le mail a bien ete envoye a la bonne adresse ;
- qu'il contient au moins une piece jointe ;
- que le message n'est pas reste en brouillon ;
- que la reponse n'est pas dans les spams.

Si rien n'arrive, transmettez l'objet du mail, l'heure d'envoi et le nom du fichier a l'administrateur.

### Le document est flou ou mal lu

Renvoyez un document plus lisible si possible. Sinon, corrigez les champs dans la page de validation avant de cliquer sur **Valider**.

### Le mauvais chantier est propose

Ne validez pas tel quel. Choisissez le bon chantier dans la page de routage.

Si le chantier n'existe pas dans la liste, signalez-le. Il doit probablement etre ajoute ou synchronise avant validation.

### Le classeur Excel est "A choisir"

Selectionnez le bon fichier dans la liste. Utilisez la saisie manuelle uniquement si on vous a donne un chemin precis.

### Le classeur Excel est "Manquant"

Ne forcez pas la validation. Signalez le blocage avec le nom du document et le message affiche.

### Le fichier est marque "deja importe"

Le systeme a reconnu un doublon. Il n'y a normalement rien a refaire.

### InterFast indique une erreur ou un blocage

Le document peut avoir ete classe dans le NAS et ecrit dans Excel, meme si InterFast bloque. Signalez le statut exact affiche sur la page pour verifier le rattachement InterFast.

Pour verifier le classement cote NAS, regardez d'abord :

```text
Professionnel_CCM > 12_AUTOMATISATION > documents > classified
```

## 11. Bonnes pratiques

- Envoyez des fichiers lisibles et complets.
- Evitez plusieurs factures fusionnees dans un seul PDF si elles doivent etre traitees separement.
- Ajoutez le chantier dans l'objet ou le corps du mail quand vous le connaissez.
- Verifiez toujours les montants avant de valider.
- Ne rejetez que les documents inutiles, doublons ou vraiment incorrects.
- Traitez les validations regulierement pour eviter l'accumulation.
- Gardez les liens de validation confidentiels.

## 12. Message type a envoyer au support

Si vous devez signaler un probleme, envoyez un message avec ces informations :

```text
Bonjour,

J'ai un probleme sur l'automatisation.

Date et heure d'envoi :
Objet du mail :
Nom du fichier :
Statut affiche :
Lien de validation si disponible :
Ce que j'attendais :
Ce qui s'est passe :

Merci.
```

Avec ces elements, l'administrateur peut retrouver le document beaucoup plus vite.

## 13. Glossaire simple

- **OCR** : lecture automatique d'un document pour extraire les informations importantes.
- **Validation** : verification humaine des informations lues par OCR.
- **Routage** : choix du chantier, du classement et des cibles Excel/InterFast.
- **Dispatch** : execution finale apres validation, avec copie de fichiers et ecritures.
- **NAS** : espace de stockage partage. Dans ce projet, les documents client sont dans `Professionnel_CCM > 12_AUTOMATISATION > documents`.
- **InterFast** : outil metier externe dans lequel certains documents peuvent etre rattaches.
- **Inexweb** : format ou destination d'export comptable selon la configuration.
- **DOE** : dossier d'ouvrage ou dossier chantier avec les pieces attendues.

## 14. Checklist avant de cliquer sur "Confirmer et ecrire dans Excel"

- Le document affiche est le bon.
- Le fournisseur est correct.
- Le numero de facture est correct.
- La date est correcte.
- Le montant HT est correct.
- La TVA est correcte.
- Le montant TTC est correct.
- Le chantier est correct.
- Les lignes Excel sont marquees **Pret** ou le bon fichier a ete choisi.
- Rien ne semble incoherent dans la previsualisation.

Si une case n'est pas claire, corrigez ou demandez verification avant de confirmer.
