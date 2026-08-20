Depose ici les certificats racine supplementaires (*.crt, format PEM) dont la
construction de l'image a besoin.

Cas d'usage : un antivirus ou un proxy d'entreprise qui intercepte le TLS. La
machine hote fait confiance a sa CA racine, le conteneur non, et `pip install`
echoue en CERTIFICATE_VERIFY_FAILED.

Le dossier est vide dans le depot : sans .crt, l'etape est un no-op. Les .crt
sont volontairement gitignores, une CA d'interception est specifique a une
machine et n'a rien a faire dans le versionnement.
