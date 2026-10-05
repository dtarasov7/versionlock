# Shared inventory resolver

`package_hold_resolve` валидирует весь `package_hold_packages` и выбирает names текущей
ОС. Используется всеми тремя ролями, null mappings исключаются. Installed state
определяется managed-host backend; filter его не угадывает.
