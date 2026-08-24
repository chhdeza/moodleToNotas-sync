"""
La aplicación de escritorio (specs/003).

Es una **capa de presentación** sobre el motor que ya existe: no reimplementa
la planificación, ni el emparejamiento de instrumentos, ni las rejas de
seguridad (A-01). Importa ``mnsync.sync`` y ``mnsync.discovery`` directamente,
sin pasar por la línea de comandos, para que un error llegue a la ventana con
su texto entero en vez de atravesar dos fronteras de subproceso (A-02).

PySide6 es una dependencia **opcional**: la línea de comandos y el flujo
automático de GitHub Actions no necesitan interfaz gráfica.

    pip install -e ".[gui]"
"""
