# ImgMetaManager

Lire, exporter, copier et supprimer les métadonnées de vos images. Une interface
web locale (Gradio) et une ligne de commande, avec le même code sur Linux,
Windows et macOS.

Pour le JPEG, le PNG et le WebP, la suppression se fait au niveau des segments du
fichier : les pixels compressés sont recopiés octet pour octet, sans réencodage
ni perte.

*[English README](README.md)*

---

## Installation

Il faut Python 3.10 ou plus récent.

### Le plus simple

```bash
git clone https://github.com/mikecastrodemaria/ImgMetaManager.git
cd ImgMetaManager
```

Puis, selon le système :

| Système | Commande |
| --- | --- |
| Linux, macOS | `./run.sh` |
| Windows | double-clic sur `run.bat` |

Le script crée l'environnement virtuel, installe les dépendances au premier
lancement, démarre le serveur local et ouvre `http://127.0.0.1:7860`.

### En paquet Python

```bash
python -m pip install -e .
imgmetamanager
```

Pour lire les photos iPhone (HEIC/HEIF) et les fichiers AVIF :

```bash
python -m pip install "imgmetamanager[heif]"
```

### Langue de l'interface

L'interface existe en français et en anglais. Elle suit la langue du système et
retombe sur l'anglais ; `--lang fr` ou `--lang en` force le choix, tout comme la
variable d'environnement `IMM_LANG`.

---

## L'interface

### Charger des images

Deux entrées, cumulables :

- onglet **Fichiers** : glissez-déposez une ou plusieurs images
- onglet **Dossier** : saisissez un chemin local (`/home/moi/Photos`,
  `C:\Users\moi\Images`, `/Users/moi/Pictures`), avec ou sans sous-dossiers

Le mode dossier travaille sur les vrais fichiers de la machine. C'est celui qui
permet de remplacer les originaux.

### Lire

L'onglet **Aperçu et métadonnées** montre l'image, un résumé (format,
dimensions, poids, position GPS, générateur d'IA détecté) et un tableau de
toutes les entrées trouvées, regroupées par famille :

| Groupe | Contenu |
| --- | --- |
| Fichier | nom, dossier, taille, date de modification, SHA-256 |
| Image | format, dimensions, mode colorimétrique, résolution, nombre de trames |
| EXIF | appareil, objectif, exposition, dates, numéros de série |
| GPS | degrés/minutes/secondes, position décimale, lien vers la carte |
| IPTC | légende, mots-clés, crédit, lieu, auteur |
| XMP | paquet Adobe aplati en couples clé/valeur |
| Texte PNG | chunks tEXt, zTXt, iTXt |
| IA générative | prompt, prompt négatif, échantillonneur, seed, modèle (A1111, ComfyUI, NovelAI) |
| Commentaires | segments COM du JPEG, commentaire GIF |
| Profil ICC | description et taille du profil couleur |
| Miniature intégrée | miniature EXIF, qui peut révéler l'image avant recadrage |

Trois filtres agissent sur le tableau : cases à cocher par groupe, recherche
plein texte sur les tags et les valeurs, et bascule « uniquement les données
sensibles ».

Une entrée est marquée sensible quand elle identifie une personne, un lieu ou un
appareil : GPS, numéros de série, nom d'auteur, MakerNote, ville, contact.

### Exporter

L'onglet **Exporter** produit un fichier téléchargeable pour l'image affichée ou
pour tout le lot :

- **JSON** : structure complète, groupée, pour un traitement automatisé
- **CSV** : une ligne par entrée, encodé UTF-8 avec BOM (Excel l'ouvre sans manipulation)
- **TXT** : rapport lisible, entrées sensibles marquées d'une étoile
- **Markdown** : tableaux par groupe, prêts pour une documentation
- **HTML** : page autonome, thème clair et sombre

Cochez « un fichier par image » pour recevoir une archive ZIP au lieu d'un
document unique. Cochez « appliquer les filtres » pour n'exporter que ce que le
tableau affiche.

Le JSON et le CSV gardent un schéma anglais stable quelle que soit la langue,
pour que les scripts qui les lisent continuent de fonctionner. Le TXT, le
Markdown et le HTML suivent la langue choisie.

### Copier

Cliquez une ligne du tableau : elle rejoint la zone **Copier des éléments**. Le
bouton « ajouter toutes les lignes » prend le tableau filtré d'un coup. Quatre
mises en forme :

```
Tag = value        Make = ACME
TSV (spreadsheet)  EXIF	Make	ACME
JSON               { "Make": "ACME" }
Values only        ACME
```

L'icône de copie de la zone de texte envoie le contenu vers le presse-papiers du
système.

### Supprimer

L'onglet **Supprimer** coche par défaut les catégories présentes dans l'image
affichée. Trois destinations :

| Destination | Effet |
| --- | --- |
| Nouveau fichier | écrit `nom-clean.ext` à côté de l'original, qui reste intact |
| Dossier de sortie | écrit les fichiers nettoyés dans le dossier de votre choix |
| Remplacer l'original | réécrit le fichier, après confirmation explicite et sauvegarde `.bak` |

La case « simuler » liste ce qui serait retiré sans rien écrire. Le rapport
indique, pour chaque fichier, les catégories supprimées, le nombre d'entrées et
le poids avant et après.

---

## Suppression sans perte

| Format | Méthode | Pixels |
| --- | --- | --- |
| JPEG | filtrage des segments APP1, APP13, COM | intacts |
| PNG | filtrage des chunks tEXt, zTXt, iTXt, eXIf | intacts |
| WebP | filtrage des chunks RIFF, drapeaux VP8X mis à jour | intacts |
| TIFF, GIF, BMP, ICO, HEIC | réencodage via Pillow | recompressés |

Le rapport affiche ✅ pour un nettoyage sans perte et ♻️ pour un réencodage.

Trois règles de sûreté :

- « Tout supprimer » épargne le profil ICC, qui pilote le rendu des couleurs.
  Cochez-le pour le retirer.
- Supprimer l'EXIF entraîne la suppression du GPS et de la miniature : ils vivent
  dans le même bloc. Retirer le GPS seul reconstruit le bloc EXIF et garde le reste.
- Les tags TIFF structurels (largeur, compression, offsets des bandes) restent en
  place. Les retirer casserait le fichier.

---

## Ligne de commande

L'interface web est le mode par défaut. Trois sous-commandes travaillent sans
navigateur.

```bash
# Interface web
imgmetamanager                          # 127.0.0.1:7860, navigateur ouvert
imgmetamanager ui --port 8080 --no-browser
imgmetamanager --lang en ui             # interface en anglais

# Lire
imgmetamanager show photo.jpg
imgmetamanager show ~/Photos -r -f json
imgmetamanager show photo.jpg --sensitive        # entrées identifiantes seules
imgmetamanager show photo.jpg -g gps exif -s iso # filtre par groupe et par mot

# Exporter
imgmetamanager export ~/Photos -r -f csv -o inventaire.csv
imgmetamanager export photo.jpg -f html -o rapport.html

# Supprimer
imgmetamanager strip ~/Photos --dry-run
imgmetamanager strip photo.jpg                        # crée photo-clean.jpg
imgmetamanager strip ~/Photos -r --out ~/Photos-propres
imgmetamanager strip photo.jpg --remove gps thumbnail
imgmetamanager strip ~/Photos --in-place              # sauvegarde .bak automatique
```

Catégories acceptées par `--remove` : `exif`, `gps`, `iptc`, `xmp`, `png_text`,
`comment`, `thumbnail`, `icc`, `other`, `all`.

Codes de sortie : `0` en cas de succès, `1` si un fichier a échoué, `2` si aucun
fichier n'a été trouvé.

---

## Vie privée et réseau

L'application tourne sur votre machine. Aucune image ne quitte le poste, aucun
appel réseau n'est fait pour analyser un fichier.

Le serveur écoute sur `127.0.0.1` par défaut. `--share` crée un lien public
temporaire via Gradio et désactive alors l'accès aux dossiers locaux ainsi que le
remplacement des originaux. Utilisez `--host 0.0.0.0` pour exposer l'application
sur votre réseau local, en connaissance de cause.

Gradio ne sert au navigateur que les fichiers situés sous les dossiers
autorisés : le dossier de travail temporaire et votre dossier personnel. Les
fichiers nettoyés hors de ces racines sont écrits sur le disque sans être
proposés au téléchargement, ce que le rapport indique.

---

## Développement

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

.venv/bin/python -m pytest tests -q     # 88 tests
.venv/bin/python -m pyflakes imgmetamanager tests
```

Le code, les commentaires et les docstrings sont en anglais. La chaîne
d'intégration continue rejoue les tests sur Ubuntu, Windows et macOS, en Python
3.10 et 3.12.

### Publier une version

Changez `version` dans `pyproject.toml`, ajoutez la section correspondante au
`CHANGELOG.md`, puis poussez sur la branche par défaut. Le workflow **Release**
pose le tag, rejoue les tests, construit le wheel et le sdist, et publie une
release GitHub dont les notes viennent du changelog. Pousser un tag `v1.2.3` ou
lancer le workflow à la main fait la même chose, et une version déjà publiée est
laissée telle quelle.

### Organisation

```
imgmetamanager/
├── app.py            interface Gradio ; MetaApp regroupe la logique, testable seule
├── cli.py            sous-commandes ui / show / export / strip
├── gr_compat.py      compatibilité Gradio 5.x et 6.x
├── i18n.py           textes anglais et français, détection de la langue
└── core/
    ├── containers.py lecture et réécriture des conteneurs JPEG, PNG, WebP
    ├── reader.py     extraction des métadonnées
    ├── writer.py     suppression, avec repli sur Pillow
    ├── exporter.py   JSON, CSV, TXT, Markdown, HTML, ZIP
    ├── tags.py       tables de tags, énumérations, formatage des valeurs
    ├── model.py      ImageMeta et MetaItem
    └── utils.py      chemins, tailles, hachage
```

Le cœur métier ne dépend pas de Gradio. `imgmetamanager.core` s'utilise comme
bibliothèque :

```python
from imgmetamanager.core import read_metadata, strip_metadata, export_metadata

meta = read_metadata("photo.jpg")
print(meta.find("Make", "exif").value)         # ACME
print(dict(meta.removable_counts()))           # {'exif': 10, 'gps': 7, ...}

rapport = strip_metadata("photo.jpg", kinds=["gps", "thumbnail"])
print(rapport.target, rapport.lossless)        # photo-clean.jpg True

export_metadata([meta], "json", "meta.json")
```

---

## Dépendances

| Paquet | Rôle |
| --- | --- |
| gradio | interface web |
| pillow | décodage des images, aperçus |
| piexif | réécriture sélective des blocs EXIF |
| defusedxml | analyse durcie des paquets XMP |
| pillow-heif | optionnel, lecture HEIC/HEIF/AVIF |

## Licence

MIT. Voir [LICENSE](LICENSE).
