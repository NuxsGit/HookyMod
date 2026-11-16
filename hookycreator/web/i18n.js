"use strict";

/* Offline UI translations. Keys are the original French text, not user asset
   names or paths. Unknown diagnostics remain intact rather than being guessed. */
const HookyI18n = (() => {
  const rows = [
    // HookyMod's: UI copy only. Workspace names, descriptions, filenames and
    // block values are user data and must never be translated.
    ["Explorer, imaginer ou assembler", "Explore, imagine or assemble", "Изучайте, придумывайте или собирайте", "探索、构思或组装"],
    ["02 / HOOKYMOD'S", "02 / HOOKYMOD'S", "02 / HOOKYMOD'S", "02 / HOOKYMOD'S"],
    ["03 / ASSEMBLAGE", "03 / ASSEMBLY", "03 / СБОРКА", "03 / 组装"],
    ["Imaginer du contenu", "Imagine new content", "Придумать содержимое", "构思新内容"],
    ["Concevoir des éléments et des procédures visuelles dans un espace indépendant.", "Design elements and visual procedures in a separate workspace.", "Создавайте элементы и визуальные процедуры в отдельном рабочем пространстве.", "在独立工作区中设计元素和可视化流程。"],
    ["Ouvrir HookyMod's", "Open HookyMod's", "Открыть HookyMod's", "打开 HookyMod's"],
    ["Assembler des modifications", "Assemble modifications", "Собрать изменения", "组装修改"],
    ["HookyMod's : atelier de création", "HookyMod's: creation studio", "HookyMod's: студия создания", "HookyMod's：创作工作室"],
    ["HOOKYMOD'S / ATELIER VISUEL", "HOOKYMOD'S / VISUAL STUDIO", "HOOKYMOD'S / ВИЗУАЛЬНАЯ СТУДИЯ", "HOOKYMOD'S / 可视化工作室"],
    ["Imagine ton prochain contenu", "Imagine your next creation", "Придумайте своё следующее творение", "构思你的下一个作品"],
    ["Un espace séparé de HookyCreator pour concevoir de nouveaux éléments.", "A separate space from HookyCreator for designing new elements.", "Отдельное от HookyCreator пространство для создания новых элементов.", "独立于 HookyCreator 的新元素设计空间。"],
    ["← Retour à HookyCreator", "← Back to HookyCreator", "← Назад в HookyCreator", "← 返回 HookyCreator"],
    ["Un atelier de conception, pour commencer.", "A design studio, to start with.", "Для начала — студия проектирования.", "从设计工作室开始。"],
    ["Les éléments et les blocs décrivent tes idées : ils ne créent pas encore de contenu jouable et ne s'exécutent pas en jeu.", "Elements and blocks describe your ideas: they do not yet create playable content or run in a game.", "Элементы и блоки описывают идеи: пока они не создают игрового содержимого и не выполняются в игре.", "元素和积木用于描述创意：目前不会创建可游玩的内容，也不会在游戏中运行。"],
    ["Longshot + Link's Tool Box : compatibilité", "Longshot + Link's Tool Box: compatibility", "Longshot + Link's Tool Box: совместимость", "Longshot + Link's Tool Box：兼容性"],
    ["La cible prévue est Longshot sur PC. L'atelier exporte des projets .hooky de conception, non jouables. Depuis les sources, un outil séparé expérimental peut emballer des fichiers de mod déjà préparés.", "The intended target is Longshot on PC. The studio exports non-playable .hooky design projects. A separate experimental source tool can package already prepared mod files.", "Целевая платформа — Longshot на ПК. Студия экспортирует проекты .hooky для проектирования, не игровые моды. Отдельный экспериментальный инструмент из исходного кода может упаковать уже подготовленные файлы мода.", "预期目标是在 PC 上使用 Longshot。工作室导出的 .hooky 设计项目不可游玩。源码中另有实验性工具可以打包已准备好的模组文件。"],
    ["Link's Tool Box importe des dossiers de mods contenant romfs. Aucun import direct .hooky par un lanceur n'a été validé ici. Ferme le jeu et sauvegarde tes mods et tes sauvegardes avant un essai.", "Link's Tool Box imports mod folders containing romfs. Direct .hooky import by a launcher has not been verified here. Close the game and back up your mods and saves before testing.", "Link's Tool Box импортирует папки модов с romfs. Прямой импорт .hooky через лаунчер здесь не проверен. Перед проверкой закройте игру и сохраните резервные копии модов и сохранений.", "Link's Tool Box 可导入包含 romfs 的模组文件夹。此处尚未验证启动器直接导入 .hooky 的功能。测试前请关闭游戏并备份模组和存档。"],
    ["PROTOTYPE", "PROTOTYPE", "ПРОТОТИП", "原型"], ["TA BIBLIOTHÈQUE", "YOUR LIBRARY", "ВАША БИБЛИОТЕКА", "你的资料库"],
    ["Espaces de travail", "Workspaces", "Рабочие пространства", "工作区"], ["Nom du nouveau projet", "New project name", "Имя нового проекта", "新项目名称"],
    ["Mon idée de mod", "My mod idea", "Моя идея мода", "我的模组创意"], ["Créer un projet", "Create a project", "Создать проект", "创建项目"],
    ["MES PROJETS", "MY PROJECTS", "МОИ ПРОЕКТЫ", "我的项目"], ["Un projet à reprendre ?", "Continue a project?", "Продолжить проект?", "继续项目？"],
    ["Importe une archive .hooky.", "Import a .hooky archive.", "Импортируйте архив .hooky.", "导入 .hooky 压缩包。"],
    ["Importer .hooky", "Import .hooky", "Импорт .hooky", "导入 .hooky"],
    ["TON ESPACE DE CRÉATION", "YOUR CREATIVE SPACE", "ВАШЕ ПРОСТРАНСТВО ДЛЯ ТВОРЧЕСТВА", "你的创作空间"],
    ["Tout commence par une idée.", "Every creation starts with an idea.", "Всё начинается с идеи.", "一切始于一个创意。"],
    ["Crée un projet à gauche ou importe un fichier .hooky pour retrouver tes idées.", "Create a project on the left or import a .hooky file to continue your ideas.", "Создайте проект слева или импортируйте файл .hooky, чтобы продолжить работу.", "在左侧创建项目，或导入 .hooky 文件继续你的创意。"],
    ["Imagine", "Imagine", "Придумайте", "构思"], ["Compose", "Compose", "Составьте", "组合"], ["Partage", "Share", "Поделитесь", "分享"],
    ["Crée ton espace de travail.", "Create your workspace.", "Создайте рабочее пространство.", "创建你的工作区。"],
    ["Ajoute des éléments et des blocs.", "Add elements and blocks.", "Добавьте элементы и блоки.", "添加元素和积木。"],
    ["Exporte ton projet .hooky.", "Export your .hooky project.", "Экспортируйте проект .hooky.", "导出你的 .hooky 项目。"],
    ["ESPACE DE TRAVAIL", "WORKSPACE", "РАБОЧЕЕ ПРОСТРАНСТВО", "工作区"],
    ["Projet de conception · non jouable", "Design project · not playable", "Проект дизайна · не является игровым", "设计项目 · 不可游玩"],
    ["Enregistrer", "Save", "Сохранить", "保存"], ["Dupliquer", "Duplicate", "Дублировать", "复制"], ["Recharger", "Reload", "Перезагрузить", "重新加载"],
    ["Vérifier", "Check", "Проверить", "检查"], ["Exporter .hooky", "Export .hooky", "Экспорт .hooky", "导出 .hooky"],
    ["Télécharger à nouveau le .hooky", "Download the .hooky again", "Скачать .hooky ещё раз", "再次下载 .hooky"],
    ["Détails du projet", "Project details", "Сведения о проекте", "项目详情"], ["Nom du projet", "Project name", "Имя проекта", "项目名称"],
    ["Auteur", "Author", "Автор", "作者"], ["Cible envisagée", "Intended target", "Предполагаемая цель", "预期目标"],
    ["Facultatif · non vérifié", "Optional · unverified", "Необязательно · не проверено", "可选 · 未验证"],
    ["Description", "Description", "Описание", "描述"], ["Supprimer ce projet", "Delete this project", "Удалить этот проект", "删除此项目"],
    ["01 / TES IDÉES", "01 / YOUR IDEAS", "01 / ВАШИ ИДЕИ", "01 / 你的创意"], ["Éléments du projet", "Project elements", "Элементы проекта", "项目元素"],
    ["Choisis une catégorie pour ajouter une nouvelle idée.", "Choose a category to add a new idea.", "Выберите категорию, чтобы добавить новую идею.", "选择类别以添加新创意。"],
    ["Objet", "Item", "Предмет", "物品"], ["Cinématique", "Cutscene", "Кат-сцена", "过场动画"], ["Donjon", "Dungeon", "Подземелье", "地牢"], ["Bâtiment", "Building", "Здание", "建筑"],
    ["Une nouvelle idée", "A new idea", "Новая идея", "一个新创意"], ["Un moment à raconter", "A moment to tell", "История для рассказа", "一个要讲述的时刻"],
    ["Un lieu à imaginer", "A place to imagine", "Место для идеи", "构思一个场所"], ["Un espace à concevoir", "A space to design", "Место для создания", "设计一个空间"],
    ["02 / TON ÉLÉMENT", "02 / YOUR ELEMENT", "02 / ВАШ ЭЛЕМЕНТ", "02 / 你的元素"], ["Détails de l'élément", "Element details", "Сведения об элементе", "元素详情"],
    ["Supprimer l'élément", "Delete element", "Удалить элемент", "删除元素"], ["Nom de l'élément", "Element name", "Имя элемента", "元素名称"],
    ["Description de l'élément", "Element description", "Описание элемента", "元素描述"], ["Ressource personnelle", "Your own asset", "Свой ресурс", "你的资源"],
    ["PNG, OBJ ou GLB · 2 Mio maximum par élément · 6 Mio par projet.", "PNG, OBJ or GLB · up to 2 MiB per element · 6 MiB per project.", "PNG, OBJ или GLB · до 2 МиБ на элемент · 6 МиБ на проект.", "PNG、OBJ 或 GLB · 每个元素最多 2 MiB · 每个项目最多 6 MiB。"],
    ["Joindre un fichier", "Attach a file", "Прикрепить файл", "附加文件"], ["Retirer", "Remove", "Удалить", "移除"],
    ["Les fichiers joints restent inchangés dans le projet ; leur compatibilité en jeu n'est pas vérifiée.", "Attached files are kept unchanged in the project; in-game compatibility is not checked.", "Прикреплённые файлы остаются неизменными; их совместимость с игрой не проверяется.", "附加文件在项目中保持不变；不会检查其游戏兼容性。"],
    ["03 / TA PROCÉDURE", "03 / YOUR PROCEDURE", "03 / ВАША ПРОЦЕДУРА", "03 / 你的流程"],
    ["Composer avec des blocs", "Compose with blocks", "Составьте из блоков", "使用积木组合"],
    ["Clique sur un bloc ou glisse-le dans la zone ; réorganise-le avec les flèches.", "Click a block or drag it into the canvas; use the arrows to reorder it.", "Нажмите на блок или перетащите его в область; используйте стрелки для изменения порядка.", "点击积木或将其拖入画布；使用箭头调整顺序。"],
    ["SIMULATION LOCALE", "LOCAL SIMULATION", "ЛОКАЛЬНАЯ СИМУЛЯЦИЯ", "本地模拟"], ["Palette de blocs", "Block palette", "Палитра блоков", "积木面板"],
    ["Zone de procédure", "Procedure canvas", "Область процедуры", "流程画布"], ["PROCÉDURE VISUELLE", "VISUAL PROCEDURE", "ВИЗУАЛЬНАЯ ПРОЦЕДУРА", "可视化流程"],
    ["Dépose un bloc ici ou clique sur la palette.", "Drop a block here or click the palette.", "Перетащите блок сюда или нажмите на палитре.", "将积木放在此处或点击面板。"],
    ["Ces blocs sont une maquette ordonnée ; seule la simulation locale les interprète. Ils ne génèrent pas de logique de jeu.", "These blocks are an ordered sketch interpreted only by the local simulator. They do not generate game logic.", "Эти блоки — упорядоченный макет, который интерпретирует только локальная симуляция. Они не создают игровую логику.", "这些积木是有序草图，仅由本地模拟器解释，不会生成游戏逻辑。"],
    ["04 / ESSAI HORS JEU", "04 / OFFLINE TEST", "04 / ПРОВЕРКА ВНЕ ИГРЫ", "04 / 游戏外测试"],
    ["Simuler la procédure", "Simulate the procedure", "Симулировать процедуру", "模拟流程"],
    ["AUCUN MOD LANCÉ", "NO MOD LAUNCHED", "МОД НЕ ЗАПУЩЕН", "未启动模组"],
    ["Les conditions filtrent les blocs suivants jusqu'au prochain événement. Les valeurs sont comparées avec les majuscules, sans les espaces en début ou fin.", "Conditions filter subsequent blocks until the next event. Values are case-sensitive; leading and trailing spaces are ignored.", "Условия фильтруют следующие блоки до следующего события. Регистр учитывается, пробелы по краям игнорируются.", "条件会筛选其后的积木，直到下一个事件。比较区分大小写，忽略首尾空格。"],
    ["Simulation en mémoire uniquement : aucune action en jeu, aucun fichier modifié et aucune attente réelle. Les valeurs initiales ne sont pas enregistrées.", "In-memory simulation only: no in-game action, file changes, or real delays. Initial values are not saved.", "Симуляция только в памяти: нет действий в игре, изменений файлов или настоящего ожидания. Начальные значения не сохраняются.", "仅在内存中模拟：不会在游戏中执行操作、修改文件或实际等待。初始值不会保存。"],
    ["Événement à simuler", "Event to simulate", "Событие для симуляции", "要模拟的事件"],
    ["Objets initiaux · un par ligne", "Initial items · one per line", "Начальные предметы · по одному в строке", "初始物品 · 每行一个"],
    ["États actifs · un par ligne", "Active states · one per line", "Активные состояния · по одному в строке", "活动状态 · 每行一个"],
    ["Simuler", "Simulate", "Симулировать", "模拟"], ["Résultat de simulation", "Simulation result", "Результат симуляции", "模拟结果"],
    ["Aucun bloc à simuler.", "No blocks to simulate.", "Нет блоков для симуляции.", "没有可模拟的积木。"],
    ["Aucun bloc ne correspond à cet événement.", "No block matches this event.", "Нет блока для этого события.", "没有积木与此事件匹配。"],
    ["Événement sélectionné", "Selected event", "Выбранное событие", "已选事件"], ["Événement non sélectionné", "Unselected event", "Невыбранное событие", "未选事件"],
    ["Condition remplie", "Condition met", "Условие выполнено", "条件满足"], ["Condition non remplie", "Condition not met", "Условие не выполнено", "条件不满足"],
    ["Ignoré", "Skipped", "Пропущено", "已跳过"], ["Valeur manquante", "Missing value", "Значение не указано", "缺少值"], ["Action simulée", "Simulated action", "Действие симулировано", "已模拟动作"],
    ["Inventaire simulé", "Simulated inventory", "Симулированный инвентарь", "模拟物品栏"], ["Portes simulées", "Simulated doors", "Симулированные двери", "模拟门"],
    ["Scènes simulées", "Simulated scenes", "Симулированные сцены", "模拟场景"], ["Aucun", "None", "Нет", "无"],
    ["Événements", "Events", "События", "事件"], ["Conditions", "Conditions", "Условия", "条件"], ["Actions", "Actions", "Действия", "动作"],
    ["Quand on entre", "When entering", "При входе", "进入时"], ["Quand on interagit", "When interacting", "При взаимодействии", "交互时"],
    ["Quand on utilise", "When using", "При использовании", "使用时"], ["Si possède un objet", "If holding an item", "Если есть предмет", "如果拥有物品"],
    ["Si un état est actif", "If a state is active", "Если состояние активно", "如果状态已激活"], ["Afficher un message", "Show a message", "Показать сообщение", "显示消息"],
    ["Donner un objet", "Give an item", "Дать предмет", "给予物品"], ["Ouvrir une porte", "Open a door", "Открыть дверь", "打开门"],
    ["Jouer une scène", "Play a scene", "Воспроизвести сцену", "播放场景"], ["Attendre", "Wait", "Подождать", "等待"],
    ["Précision facultative", "Optional detail", "Необязательное уточнение", "可选说明"], ["Valeur de conception", "Design value", "Значение для макета", "设计值"],
    ["Valeur du bloc", "Block value", "Значение блока", "积木值"], ["Monter le bloc", "Move block up", "Переместить блок выше", "上移积木"],
    ["Descendre le bloc", "Move block down", "Переместить блок ниже", "下移积木"], ["Retirer le bloc", "Remove block", "Удалить блок", "移除积木"],
    ["Sans nom", "Untitled", "Без названия", "未命名"], ["élément(s)", "element(s)", "элемент(ов)", "个元素"], ["projet(s) illisible(s)", "unreadable project(s)", "нечитаемых проектов", "个无法读取的项目"],
    ["Aucun projet pour l'instant.", "No projects yet.", "Проектов пока нет.", "暂无项目。"], ["Chargement des projets…", "Loading projects…", "Загрузка проектов…", "正在加载项目…"],
    ["Aucun élément pour l'instant.", "No elements yet.", "Элементов пока нет.", "暂无元素。"],
    ["Opération en cours…", "Operation in progress…", "Операция выполняется…", "正在操作…"], ["Modifications non enregistrées", "Unsaved changes", "Несохранённые изменения", "未保存的更改"],
    ["Tout est enregistré", "All changes saved", "Все изменения сохранены", "所有更改已保存"], ["Opération impossible.", "Operation failed.", "Операция невозможна.", "操作失败。"],
    ["Ce projet contient des modifications non enregistrées. Les abandonner ?", "This project has unsaved changes. Discard them?", "В проекте есть несохранённые изменения. Отменить их?", "此项目有未保存的更改。放弃更改吗？"],
    ["Supprimer définitivement ce projet et ses éléments, y compris les modifications non enregistrées ?", "Permanently delete this project and its elements, including unsaved changes?", "Удалить проект и элементы навсегда, включая несохранённые изменения?", "永久删除此项目及其元素，包括未保存的更改吗？"],
    ["Supprimer cet élément et ses blocs ?", "Delete this element and its blocks?", "Удалить элемент и его блоки?", "删除此元素及其积木吗？"],
    ["Donne un nom au projet.", "Give the project a name.", "Назовите проект.", "请为项目命名。"],
    ["Limite de 100 éléments atteinte.", "The limit of 100 elements has been reached.", "Достигнут предел в 100 элементов.", "已达到 100 个元素的限制。"],
    ["Limite de 100 blocs atteinte.", "The limit of 100 blocks has been reached.", "Достигнут предел в 100 блоков.", "已达到 100 个积木的限制。"],
    ["Générateur d'identifiants indisponible.", "ID generator unavailable.", "Генератор идентификаторов недоступен.", "标识符生成器不可用。"],
    ["Choisis un PNG, OBJ ou GLB non vide de 2 Mio maximum.", "Choose a nonempty PNG, OBJ or GLB of at most 2 MiB.", "Выберите непустой PNG, OBJ или GLB не более 2 МиБ.", "请选择不超过 2 MiB 的非空 PNG、OBJ 或 GLB 文件。"],
    ["Les ressources dépassent 6 Mio au total.", "Assets exceed 6 MiB in total.", "Общий размер ресурсов превышает 6 МиБ.", "资源总大小超过 6 MiB。"],
    ["Choisis un fichier .hooky non vide de 12 Mio maximum.", "Choose a nonempty .hooky file of at most 12 MiB.", "Выберите непустой файл .hooky не более 12 МиБ.", "请选择不超过 12 MiB 的非空 .hooky 文件。"],
    ["Projet importé dans un nouvel espace.", "Project imported into a new workspace.", "Проект импортирован в новое пространство.", "项目已导入新工作区。"],
    ["Copie créée dans un nouvel espace.", "Copy created in a new workspace.", "Копия создана в новом пространстве.", "副本已创建在新工作区中。"],
    ["Vérification du schéma", "Design check", "Проверка схемы", "设计检查"],
    ["Projet de conception uniquement : aucun générateur ou lanceur en jeu n'est encore validé.", "Design project only: no game generator or launcher has been validated yet.", "Только проект дизайна: генератор и запуск в игре пока не проверены.", "仅为设计项目：尚未验证游戏生成器或启动器。"],
    ["Pas d'avertissement de conception.", "No design warnings.", "Замечаний к схеме нет.", "没有设计警告。"],
    ["La session, les réglages ou le projet HookyMod's n'ont pas pu être enregistrés. Quitter quand même ?", "The session, settings or HookyMod's project could not be saved. Quit anyway?", "Не удалось сохранить сеанс, настройки или проект HookyMod's. Всё равно выйти?", "无法保存会话、设置或 HookyMod's 项目。仍要退出吗？"],
    ["Fichier", "File", "Файл", "文件"], ["Édition", "Edit", "Правка", "编辑"],
    ["Modèle", "Model", "Модель", "模型"], ["Modèles", "Models", "Модели", "模型"],
    ["Texture", "Texture", "Текстура", "纹理"], ["Textures", "Textures", "Текстуры", "纹理"],
    ["Importer", "Import", "Импорт", "导入"], ["Affichage", "View", "Вид", "视图"],
    ["Aide", "Help", "Помощь", "帮助"], ["commandes", "commands", "команды", "命令"],
    ["0 sélectionné", "0 selected", "Выбрано: 0", "已选择 0 项"],
    ["Exporter en ZIP", "Export ZIP", "Экспорт ZIP", "导出 ZIP"], ["Tout", "All", "Все", "全部"],
    ["Inverser", "Invert", "Инвертировать", "反选"], ["Vider", "Clear", "Очистить", "清空"],
    ["Accueil", "Home", "Главная", "主页"], ["Modifier", "Edit", "Изменить", "修改"],
    ["Modification", "Edit", "Редактирование", "修改"], ["Atelier", "Workshop", "Мастерская", "工作区"],
    ["Ensembles", "Collections", "Наборы", "资源集"], ["Animations", "Animations", "Анимации", "动画"],
    ["Animation", "Animation", "Анимация", "动画"], ["Vidéos", "Videos", "Видео", "视频"],
    ["Vidéo", "Video", "Видео", "视频"], ["Sons", "Sounds", "Звуки", "声音"],
    ["Son", "Sound", "Звук", "声音"], ["Créer", "Create", "Создать", "创建"],
    ["Mon mod", "My mod", "Мой мод", "我的模组"], ["Création de mod", "Mod creation", "Создание мода", "创建模组"],
    ["Mobs", "Mobs", "Мобы", "生物"], ["Outils", "Tools", "Инструменты", "工具"],
    ["Réglages", "Settings", "Настройки", "设置"], ["Journal", "Log", "Журнал", "日志"],
    ["Chargement du projet…", "Loading project…", "Загрузка проекта…", "正在加载项目…"],
    ["Choisis ton espace", "Choose a workspace", "Выберите рабочую область", "选择工作区"],
    ["Deux étapes, un même projet", "Two steps, one project", "Два этапа, один проект", "两个步骤，一个项目"],
    ["01 / ATELIER", "01 / WORKSHOP", "01 / МАСТЕРСКАЯ", "01 / 工作区"],
    ["Modifier l'existant", "Edit existing assets", "Изменить существующие ресурсы", "修改现有资源"],
    ["Explorer les modèles, retoucher les textures et travailler avec Blender.", "Explore models, edit textures and work with Blender.", "Изучайте модели, редактируйте текстуры и работайте в Blender.", "浏览模型、编辑纹理并使用 Blender。"],
    ["Ouvrir la modification", "Open editing", "Открыть редактор", "打开修改界面"],
    ["02 / PROJET DE MOD", "02 / MOD PROJECT", "02 / ПРОЕКТ МОДА", "02 / 模组项目"],
    ["Créer un mod", "Create a mod", "Создать мод", "创建模组"],
    ["Préparer des fiches et assembler les fichiers modifiés dans un dossier de mod.", "Prepare records and assemble modified files into a mod folder.", "Подготовьте записи и соберите изменённые файлы в папку мода.", "准备条目并将修改后的文件组装到模组文件夹。"],
    ["Ouvrir la création", "Open creation", "Открыть создание", "打开创建界面"],
    ["Projet", "Project", "Проект", "项目"], ["Export complet", "Full export", "Полный экспорт", "完整导出"],
    ["BFRES auxiliaires (rapport)", "Auxiliary BFRES (report)", "Вспомогательные BFRES (отчёт)", "辅助 BFRES（报告）"],
    ["Exporte les textures reconnues en PNG et les modèles en glTF et OBJ, y compris dans les packs. Les BFRES auxiliaires ne sont pas encore exportés.", "Export recognized textures as PNG and models as glTF and OBJ, including inside packs. Auxiliary BFRES files are not yet exported.", "Экспорт распознанных текстур в PNG и моделей в glTF и OBJ, включая файлы в пакетах. Вспомогательные BFRES пока не экспортируются.", "将识别出的纹理导出为 PNG，模型导出为 glTF 和 OBJ，包括包内资源。辅助 BFRES 文件尚不支持导出。"],
    ["Lancer l'export complet", "Start full export", "Начать полный экспорт", "开始完整导出"],
    ["Dernières actions", "Recent actions", "Последние действия", "最近操作"],
    ["ESPACE 01 / MODIFICATION", "WORKSPACE 01 / EDIT", "ОБЛАСТЬ 01 / РЕДАКТИРОВАНИЕ", "工作区 01 / 修改"],
    ["Explore les ressources du projet et travaille sur des copies, sans écraser les fichiers source.", "Explore project assets and work on copies without overwriting source files.", "Изучайте ресурсы проекта и работайте с копиями, не перезаписывая исходные файлы.", "浏览项目资源并处理副本，不覆盖源文件。"],
    ["Vers la création du mod →", "Go to mod creation →", "К созданию мода →", "前往创建模组 →"],
    ["Que veux-tu modifier ou explorer ?", "What would you like to edit or explore?", "Что вы хотите изменить или изучить?", "要修改或浏览什么？"],
    ["Inspecter, prévisualiser et exporter la géométrie reconnue.", "Inspect, preview and export recognized geometry.", "Изучайте, просматривайте и экспортируйте распознанную геометрию.", "检查、预览并导出识别出的几何体。"],
    ["Retrouver les textures et animations associées aux modèles.", "Find textures and animations associated with models.", "Найдите текстуры и анимации, связанные с моделями.", "查找与模型关联的纹理和动画。"],
    ["Exporter ou remplacer une image dans les formats pris en charge.", "Export or replace an image in supported formats.", "Экспортируйте или замените изображение в поддерживаемых форматах.", "以支持的格式导出或替换图像。"],
    ["Sons (expérimental)", "Sounds (experimental)", "Звуки (экспериментально)", "声音（实验性）"],
    ["Écouter, exporter et remplacer les sons BWAV compatibles.", "Play, export and replace compatible BWAV sounds.", "Прослушивайте, экспортируйте и заменяйте совместимые звуки BWAV.", "播放、导出和替换兼容的 BWAV 声音。"],
    ["Importer un modèle", "Import a model", "Импорт модели", "导入模型"],
    ["Analyser un OBJ ou glTF et injecter sa géométrie dans une cible compatible.", "Analyze an OBJ or glTF and inject its geometry into a compatible target.", "Проанализируйте OBJ или glTF и внедрите геометрию в совместимую цель.", "分析 OBJ 或 glTF，并将其几何体注入兼容的目标。"],
    ["Les originaux restent intacts.", "The originals remain intact.", "Оригиналы остаются нетронутыми.", "原始文件保持不变。"],
    ["Les modifications sont enregistrées dans le dossier de travail ; assemble-les ensuite depuis l'espace création de mod.", "Changes are saved in the work folder; assemble them from the mod creation workspace.", "Изменения сохраняются в рабочей папке; затем соберите их в области создания мода.", "更改保存在工作文件夹中；然后在模组创建区组装。"],
    ["ESPACE 02 / CRÉATION DE MOD", "WORKSPACE 02 / MOD CREATION", "ОБЛАСТЬ 02 / СОЗДАНИЕ МОДА", "工作区 02 / 创建模组"],
    ["Rassemble tes modifications et prépare un dossier de mod à partir du projet ouvert.", "Collect your changes and prepare a mod folder from the open project.", "Соберите изменения и подготовьте папку мода из открытого проекта.", "汇总更改，并根据当前项目准备模组文件夹。"],
    ["← Vers la modification", "← Back to editing", "← К редактированию", "← 返回修改"],
    ["Fichiers du projet", "Project files", "Файлы проекта", "项目文件"],
    ["Les copies modifiées dans l'atelier seront incluses dans l'assemblage.", "Modified working copies will be included in the build.", "Изменённые рабочие копии будут включены в сборку.", "修改过的工作副本将包含在组装结果中。"],
    ["Ouvrir la modification →", "Open editing →", "Открыть редактор →", "打开修改界面 →"],
    ["Fiches de mobs", "Mob records", "Записи мобов", "生物条目"],
    ["Prépare une fiche et copie un modèle et des textures sous un nouveau nom.", "Prepare a record and copy a model and textures under a new name.", "Подготовьте запись и скопируйте модель и текстуры под новым именем.", "准备条目，并以新名称复制模型和纹理。"],
    ["C'est une préparation de fichiers, pas la création automatique d'un acteur jouable.", "This prepares files; it does not automatically create a playable actor.", "Это подготовка файлов, а не автоматическое создание игрового персонажа.", "这只准备文件，不会自动创建可在游戏中使用的角色。"],
    ["Gérer les fiches →", "Manage records →", "Управление записями →", "管理条目 →"],
    ["Assembler le mod", "Build the mod", "Собрать мод", "组装模组"], ["Nom du mod", "Mod name", "Имя мода", "模组名称"],
    ["Le nom du dossier conserve uniquement les lettres, chiffres, tirets et caractères", "The folder name keeps only letters, numbers, hyphens and", "В имени папки сохраняются только буквы, цифры, дефисы и", "文件夹名称仅保留字母、数字、连字符和字符"],
    ["; les espaces sont retirés. S'il ne reste rien, le nom utilisé est", "; spaces are removed. If nothing remains, the name used is", "; пробелы удаляются. Если ничего не осталось, используется имя", "；空格会被删除。如果没有剩余字符，则使用名称"],
    ["Attention : le dossier", "Warning: the folder", "Внимание: папка", "注意：文件夹"],
    [", s'il existe déjà, sera entièrement remplacé, sans sauvegarde.", ", if it already exists, will be completely replaced without a backup.", ", если она уже существует, будет полностью заменена без резервной копии.", "如果已存在，将被完全替换，且不会备份。"],
    ["Assembler dans", "Build into", "Собрать в", "组装到"], ["Résultat de l'assemblage", "Build result", "Результат сборки", "组装结果"],
    ["Les copies sont rangées dans", "Copies are stored in", "Копии сохраняются в", "副本保存在"],
    [", avec un manifeste. L'assemblage ne met pas à jour la table RESTBL : vérifie les tailles et teste en jeu.", ", with a manifest. Building does not update RESTBL: check sizes and test in game.", ", вместе с манифестом. Сборка не обновляет RESTBL: проверьте размеры и протестируйте в игре.", "，并附有清单。组装不会更新 RESTBL：请检查大小并在游戏中测试。"],
    ["Pour un nouveau mob, il reste du travail côté jeu.", "A new mob still requires game-side work.", "Для нового моба необходима дополнительная работа на стороне игры.", "新生物仍需要在游戏端进行其他工作。"],
    ["La fiche ne crée ni paramètre d'acteur BYML ni placement sur la carte, et ne met pas à jour RESTBL ou les noms internes du modèle.", "The record creates neither BYML actor parameters nor map placement, and does not update RESTBL or internal model names.", "Запись не создаёт параметры актора BYML или размещение на карте и не обновляет RESTBL и внутренние имена модели.", "条目不会创建 BYML 角色参数或地图位置，也不会更新 RESTBL 或模型内部名称。"],
    ["Réessayer", "Retry", "Повторить", "重试"], ["vignettes 3D", "3D thumbnails", "3D-миниатюры", "3D 缩略图"],
    ["Afficher plus", "Show more", "Показать ещё", "显示更多"],
    ["Modèles et fichiers associés, regroupés par nom. L'aperçu n'anime pas les ressources.", "Models and associated files grouped by name. This collection preview is static.", "Модели и связанные файлы, сгруппированные по имени. Предпросмотр набора статичен.", "按名称分组的模型和关联文件。资源集预览为静态图像。"],
    ["couleurs des textures", "texture colors", "цвета текстур", "纹理颜色"],
    ["expérimental", "experimental", "экспериментально", "实验性"],
    ["Ouvre un fichier FSKA, choisis un clip et un modèle compatible pour prévisualiser le mouvement. Les courbes et l'association des os restent expérimentales ; l'export du modèle reste statique.", "Open an FSKA file, select a clip and compatible model to preview motion. Curves and bone matching are experimental; model exports remain static.", "Откройте FSKA, выберите клип и совместимую модель для предпросмотра движения. Кривые и сопоставление костей экспериментальны; экспорт модели остаётся статичным.", "打开 FSKA 文件，选择片段和兼容模型以预览动作。曲线和骨骼匹配仍处于实验阶段；导出的模型仍为静态。"],
    ["Toutes les cartes", "All maps", "Все карты", "所有贴图"], ["Albédo (Alb)", "Albedo (Alb)", "Альбедо (Alb)", "反照率 (Alb)"],
    ["Normale (Nrm)", "Normal (Nrm)", "Нормали (Nrm)", "法线 (Nrm)"], ["Rugosité (Rgh)", "Roughness (Rgh)", "Шероховатость (Rgh)", "粗糙度 (Rgh)"],
    ["Métal (Mtl)", "Metallic (Mtl)", "Металличность (Mtl)", "金属度 (Mtl)"], ["Occlusion (AO)", "Occlusion (AO)", "Окклюзия (AO)", "遮蔽 (AO)"],
    ["Émission (Emi)", "Emissive (Emi)", "Свечение (Emi)", "自发光 (Emi)"], ["Spéculaire (Spc)", "Specular (Spc)", "Блик (Spc)", "高光 (Spc)"],
    ["Masque (Msk)", "Mask (Msk)", "Маска (Msk)", "遮罩 (Msk)"], ["Inconnu (?)", "Unknown (?)", "Неизвестно (?)", "未知 (?)"],
    ["BWAV PCM16 ou DSP-ADPCM mono/stéréo et Opus mono : écoute et export WAV, MP3, FLAC ou Ogg. Un remplacement écrit une copie BWAV PCM16 expérimentale ; les banques BARS et la compatibilité en jeu ne sont pas validées.", "Mono/stereo BWAV PCM16 or DSP-ADPCM and mono Opus: playback and WAV, MP3, FLAC or Ogg export. Replacement writes an experimental PCM16 BWAV copy; BARS banks and in-game compatibility are unverified.", "Моно/стерео BWAV PCM16 или DSP-ADPCM и моно Opus: прослушивание и экспорт WAV, MP3, FLAC или Ogg. Замена создаёт экспериментальную копию PCM16 BWAV; банки BARS и совместимость с игрой не проверены.", "支持播放单声道/立体声 BWAV PCM16、DSP-ADPCM 和单声道 Opus，并导出 WAV、MP3、FLAC 或 Ogg。替换会写入实验性的 PCM16 BWAV 副本；BARS 音频库和游戏内兼容性尚未验证。"],
    ["Vérifier plus de sons", "Check more sounds", "Проверить больше звуков", "检查更多声音"],
    ["Prépare une fiche en copiant des ressources existantes sous un nouveau nom.", "Prepare a record by copying existing assets under a new name.", "Подготовьте запись, скопировав существующие ресурсы под новым именем.", "以新名称复制现有资源以准备条目。"],
    ["← Retour à la création du mod", "← Back to mod creation", "← Вернуться к созданию мода", "← 返回模组创建"],
    ["Fiche préparatoire uniquement.", "Preparatory record only.", "Только подготовительная запись.", "仅为准备性条目。"],
    ["Les noms internes des ressources ne sont pas modifiés et aucun acteur jouable, placement ni entrée RESTBL n'est créé automatiquement.", "Internal asset names are not changed, and no playable actor, placement or RESTBL entry is created automatically.", "Внутренние имена ресурсов не меняются; игровой актор, размещение и запись RESTBL автоматически не создаются.", "不会更改资源内部名称，也不会自动创建游戏角色、位置或 RESTBL 条目。"],
    ["Nouveau mob", "New mob", "Новый моб", "新生物"], ["Nom affiché", "Display name", "Отображаемое имя", "显示名称"],
    ["Parent à noter sur la fiche (facultatif)", "Parent to note on the record (optional)", "Родитель для записи (необязательно)", "条目中记录的父项（可选）"],
    ["Rechercher une ressource", "Search assets", "Поиск ресурса", "搜索资源"], ["Modèle source", "Source model", "Исходная модель", "源模型"],
    ["Textures (plusieurs possibles)", "Textures (multiple allowed)", "Текстуры (можно несколько)", "纹理（可多选）"],
    ["Seules les ressources décodées sont proposées, progressivement.", "Only decoded assets are offered, as they are verified.", "Предлагаются только декодированные ресурсы по мере проверки.", "仅逐步显示已解码的资源。"],
    ["Vérifier 48 ressources de plus", "Check 48 more assets", "Проверить ещё 48 ресурсов", "再检查 48 个资源"],
    ["Créer la fiche", "Create record", "Создать запись", "创建条目"], ["Fiches existantes", "Existing records", "Существующие записи", "现有条目"],
    ["OUTIL / GÉOMÉTRIE", "TOOL / GEOMETRY", "ИНСТРУМЕНТ / ГЕОМЕТРИЯ", "工具 / 几何体"],
    ["Prévisualise un OBJ, glTF ou GLB triangulé puis injecte sa géométrie dans une copie de travail d'un modèle compatible.", "Preview a triangulated OBJ, glTF or GLB, then inject its geometry into a working copy of a compatible model.", "Просмотрите триангулированный OBJ, glTF или GLB и внедрите геометрию в рабочую копию совместимой модели.", "预览已三角化的 OBJ、glTF 或 GLB，然后将几何体注入兼容模型的工作副本。"],
    ["Compatibilité limitée.", "Limited compatibility.", "Ограниченная совместимость.", "兼容性有限。"],
    ["Seules certaines structures BFRES 9.x sont réécrites ; l'écriture BFRES 10.x n'est pas disponible. La cible doit avoir une seule forme et un seul niveau de détail, sans squelette, déformation ni bornes à réécrire. La géométrie doit tenir dans les tampons existants. Seuls les matériaux et textures de la cible sont conservés.", "Only certain BFRES 9.x structures can be rewritten; BFRES 10.x writing is unavailable. The target must have one shape and one LOD, with no skeleton, deformations or bounds to rewrite. Geometry must fit existing buffers. Only the target's materials and textures are retained.", "Перезаписываются только некоторые структуры BFRES 9.x; запись BFRES 10.x недоступна. Цель должна иметь одну форму и один уровень детализации, без скелета, деформаций и требующих перезаписи границ. Геометрия должна помещаться в существующие буферы. Сохраняются только материалы и текстуры цели.", "仅能重写部分 BFRES 9.x 结构；不支持写入 BFRES 10.x。目标必须只有一个形状和一个细节层级，且没有需要重写的骨架、变形或边界。几何体必须放入现有缓冲区。只保留目标的材质和纹理。"],
    ["Dépose tes fichiers ici", "Drop files here", "Перетащите файлы сюда", "将文件拖放到此处"],
    ["OBJ, glTF ou GLB · 64 Mio maximum au total", "OBJ, glTF or GLB · 64 MiB total maximum", "OBJ, glTF или GLB · не более 64 МиБ всего", "OBJ、glTF 或 GLB · 总计最多 64 MiB"],
    ["Choisir les fichiers…", "Choose files…", "Выбрать файлы…", "选择文件…"],
    ["Pour un glTF avec tampons externes, sélectionne aussi les fichiers .bin associés. Les textures et matériaux du fichier source ne sont pas importés. Les tangentes, couleurs et UV secondaires non conservables bloquent l'injection.", "For glTF with external buffers, also select the associated .bin files. Source textures and materials are not imported. Tangents, colors and secondary UVs that cannot be retained block injection.", "Для glTF с внешними буферами выберите также связанные файлы .bin. Текстуры и материалы источника не импортируются. Тангенсы, цвета и дополнительные UV, которые нельзя сохранить, блокируют внедрение.", "对于使用外部缓冲区的 glTF，请同时选择关联的 .bin 文件。不会导入源文件的纹理和材质。无法保留的切线、颜色和次级 UV 会阻止注入。"],
    ["Aucun fichier choisi", "No file selected", "Файл не выбран", "未选择文件"], ["Analyser et prévisualiser", "Analyze and preview", "Анализ и предпросмотр", "分析并预览"],
    ["Cible et transformation", "Target and transform", "Цель и преобразование", "目标和变换"], ["Axes du fichier source", "Source file axes", "Оси исходного файла", "源文件坐标轴"],
    ["Conserver les axes (glTF Y vers le haut)", "Keep axes (glTF Y-up)", "Сохранить оси (glTF, Y вверх)", "保留坐标轴（glTF Y 轴向上）"],
    ["Convertir Z vers le haut en Y vers le haut", "Convert Z-up to Y-up", "Преобразовать Z вверх в Y вверх", "将 Z 轴向上转换为 Y 轴向上"],
    ["Échelle", "Scale", "Масштаб", "缩放"], ["adapter à la taille et au centre de la cible", "fit to target size and center", "подогнать под размер и центр цели", "适配目标的大小和中心"],
    ["Rechercher le modèle cible", "Search target model", "Найти целевую модель", "搜索目标模型"], ["Modèle cible", "Target model", "Целевая модель", "目标模型"],
    ["Seules les cibles vérifiées pour cette écriture limitée sont proposées. L'absence de cible compatible est possible.", "Only targets verified for this limited writer are offered. There may be no compatible target.", "Предлагаются только цели, проверенные для ограниченной записи. Совместимой цели может не оказаться.", "仅显示针对这种有限写入方式验证过的目标。可能没有兼容的目标。"],
    ["Vérifier 48 modèles de plus", "Check 48 more models", "Проверить ещё 48 моделей", "再检查 48 个模型"],
    ["Injecter dans la copie de travail", "Inject into working copy", "Внедрить в рабочую копию", "注入工作副本"],
    ["La source du projet reste intacte. Assemble ensuite les modifications dans « Mon mod ».", "The project source remains intact. Assemble changes from “My mod”.", "Источник проекта остаётся нетронутым. Затем соберите изменения в разделе «Мой мод».", "项目源文件保持不变。然后在“我的模组”中组装更改。"],
    ["Aperçu et rapport", "Preview and report", "Предпросмотр и отчёт", "预览和报告"], ["Source analysée", "Analyzed source", "Проанализированный источник", "已分析的源文件"],
    ["Copie modifiée", "Modified copy", "Изменённая копия", "修改后的副本"], ["Le rapport apparaîtra ici après l'analyse.", "The report will appear here after analysis.", "Отчёт появится здесь после анализа.", "分析后报告将显示在这里。"],
    ["Le budget décide de ce que l'aperçu s'autorise. Rien n'est envoyé ailleurs : tout est écrit dans", "The budget limits preview resources. Nothing is sent elsewhere: everything is written to", "Бюджет ограничивает ресурсы предпросмотра. Ничего никуда не отправляется: всё записывается в", "预算限制预览资源。不会发送任何内容：所有内容写入"],
    ["du dossier de travail.", "in the work folder.", "в рабочей папке.", "（位于工作文件夹中）。"], ["Valeurs par défaut", "Defaults", "Значения по умолчанию", "默认值"],
    ["Mémoire", "Memory", "Память", "内存"], ["Budget des aperçus", "Preview budget", "Бюджет предпросмотра", "预览预算"],
    ["6 Go évite les plantages sur un poste modeste ; au-delà, les très gros modèles s'affichent plus finement.", "6 GiB helps avoid crashes on modest machines; a larger budget allows more demanding previews.", "6 ГиБ помогают избежать сбоев на слабых компьютерах; больший бюджет позволяет показывать более сложные модели.", "6 GiB 有助于避免普通电脑崩溃；更高预算可支持更复杂的预览。"],
    ["Anticrénelage", "Antialiasing", "Сглаживание", "抗锯齿"],
    ["Les vues conservent toutes les faces décodées ; si le budget ne suffit pas, le PNG est refusé.", "Views retain all decoded faces; PNG rendering is refused if the budget is insufficient.", "Все декодированные грани сохраняются; при недостатке бюджета создание PNG отклоняется.", "视图保留所有已解码的面；预算不足时会拒绝生成 PNG。"],
    ["culling des faces vues de dos", "cull back faces", "отсечение обратных граней", "剔除背面"],
    ["culling des faces hors écran", "cull off-screen faces", "отсечение граней вне экрана", "剔除屏幕外的面"],
    ["réduire l'anticrénelage si nécessaire", "reduce antialiasing if necessary", "уменьшить сглаживание при необходимости", "必要时降低抗锯齿"],
    ["Interface", "Interface", "Интерфейс", "界面"], ["Thème", "Theme", "Тема", "主题"],
    ["Clair", "Light", "Светлая", "浅色"], ["Sombre", "Dark", "Тёмная", "深色"], ["Langue", "Language", "Язык", "语言"],
    ["Français", "French", "Французский", "法语"], ["English", "English", "Английский", "英语"],
    ["Couleur d'accent", "Accent color", "Акцентный цвет", "强调色"], ["Densité", "Density", "Плотность", "密度"],
    ["menthe", "Mint", "Мята", "薄荷绿"], ["azur", "Azure", "Лазурный", "天蓝"], ["ambre", "Amber", "Янтарный", "琥珀色"], ["framboise", "Raspberry", "Малиновый", "莓红"],
    ["confort", "Comfortable", "Удобная", "舒适"], ["compact", "Compact", "Компактная", "紧凑"],
    ["colorer les aperçus avec les textures du modèle", "color previews using model textures", "окрашивать предпросмотр текстурами модели", "使用模型纹理为预览上色"],
    ["joindre les PNG aux exports GLB / glTF / OBJ", "include PNGs with GLB / glTF / OBJ exports", "добавлять PNG к экспорту GLB / glTF / OBJ", "在 GLB / glTF / OBJ 导出中包含 PNG"],
    ["Moteur & performances", "Engine & performance", "Движок и производительность", "引擎和性能"],
    ["Vérification du moteur…", "Checking engine…", "Проверка движка…", "正在检查引擎…"],
    ["Portage progressif : les fonctions qui ne sont pas encore en Rust utilisent le moteur Python existant.", "Incremental port: features not yet in Rust use the existing Python engine.", "Постепенный перенос: функции, ещё не реализованные на Rust, используют существующий движок Python.", "逐步移植：尚未使用 Rust 实现的功能会使用现有 Python 引擎。"],
    ["Mémoire & cache", "Memory & cache", "Память и кэш", "内存和缓存"], ["Cache de vignettes", "Thumbnail cache", "Кэш миниатюр", "缩略图缓存"],
    ["Vider le cache", "Clear cache", "Очистить кэш", "清除缓存"], ["Rescanner le projet", "Rescan project", "Пересканировать проект", "重新扫描项目"],
    ["Préparer les vignettes", "Prepare thumbnails", "Подготовить миниатюры", "准备缩略图"], ["Arrêter", "Stop", "Остановить", "停止"],
    ["Préparation non lancée.", "Preparation not started.", "Подготовка не запущена.", "尚未开始准备。"],
    ["La préparation complète est facultative. Les vignettes visibles passent d'abord et le cache est réutilisé à la prochaine ouverture.", "Full preparation is optional. Visible thumbnails have priority, and the cache is reused next time.", "Полная подготовка необязательна. Видимые миниатюры имеют приоритет; кэш используется повторно.", "完整准备是可选的。可见缩略图优先处理，缓存将在下次打开时重用。"],
    ["Résumé", "Summary", "Сводка", "摘要"], ["vider", "clear", "очистить", "清空"], ["fermer", "close", "закрыть", "关闭"],
    ["Détail", "Details", "Подробности", "详情"], ["prêt", "ready", "готово", "就绪"], ["⏸ Vignettes", "⏸ Thumbnails", "⏸ Миниатюры", "⏸ 缩略图"],
    ["⚠ Scan incomplet", "⚠ Incomplete scan", "⚠ Неполное сканирование", "⚠ 扫描不完整"],
    ["Export prêt", "Export ready", "Экспорт готов", "导出已就绪"], ["Le dossier sur cet ordinateur :", "Folder on this computer:", "Папка на этом компьютере:", "此计算机上的文件夹："],
    ["Télécharger le ZIP", "Download ZIP", "Скачать ZIP", "下载 ZIP"],
    ["Si le téléchargement ne démarre pas, utilise le bouton ou ouvre ce dossier.", "If the download does not start, use the button or open this folder.", "Если загрузка не началась, воспользуйтесь кнопкой или откройте эту папку.", "如果下载没有开始，请使用按钮或打开此文件夹。"],
    ["Quitter l'atelier (arrête le serveur)", "Quit workshop (stops the server)", "Выйти из мастерской (остановить сервер)", "退出工作区（停止服务器）"],
    ["Réduire l'interface (mode compact, sans rail)", "Reduce interface (compact mode, no sidebar)", "Свернуть интерфейс (компактный режим без панели)", "缩小界面（紧凑模式，隐藏侧栏）"],
    ["Plein écran", "Fullscreen", "Полный экран", "全屏"], ["Palette de commandes (Ctrl+K)", "Command palette (Ctrl+K)", "Палитра команд (Ctrl+K)", "命令面板 (Ctrl+K)"],
    ["Projet ouvert", "Open project", "Открытый проект", "已打开项目"], ["Accueil (Ctrl+1)", "Home (Ctrl+1)", "Главная (Ctrl+1)", "主页 (Ctrl+1)"],
    ["Espace modification", "Editing workspace", "Область редактирования", "修改工作区"], ["Modèles (Ctrl+2)", "Models (Ctrl+2)", "Модели (Ctrl+2)", "模型 (Ctrl+2)"],
    ["Ensembles : modèle + textures + animations", "Collections: model + textures + animations", "Наборы: модель + текстуры + анимации", "资源集：模型 + 纹理 + 动画"],
    ["Animations squelettiques (expérimental)", "Skeletal animations (experimental)", "Скелетная анимация (экспериментально)", "骨骼动画（实验性）"],
    ["Textures (Ctrl+3)", "Textures (Ctrl+3)", "Текстуры (Ctrl+3)", "纹理 (Ctrl+3)"], ["Sons BWAV (expérimental)", "BWAV sounds (experimental)", "Звуки BWAV (экспериментально)", "BWAV 声音（实验性）"],
    ["Importer un modèle (Ctrl+5)", "Import a model (Ctrl+5)", "Импорт модели (Ctrl+5)", "导入模型 (Ctrl+5)"], ["Espace création de mod", "Mod creation workspace", "Область создания мода", "模组创建工作区"],
    ["Fiches de mobs (Ctrl+4)", "Mob records (Ctrl+4)", "Записи мобов (Ctrl+4)", "生物条目 (Ctrl+4)"], ["Réglages (Ctrl+6)", "Settings (Ctrl+6)", "Настройки (Ctrl+6)", "设置 (Ctrl+6)"], ["Journal (Ctrl+J)", "Log (Ctrl+J)", "Журнал (Ctrl+J)", "日志 (Ctrl+J)"],
    ["Ces fichiers seront signalés comme ignorés dans le rapport", "These files will be marked as skipped in the report", "Эти файлы будут отмечены в отчёте как пропущенные", "这些文件将在报告中标记为已跳过"],
    ["Filtrer… (Ctrl+F)", "Filter… (Ctrl+F)", "Фильтр… (Ctrl+F)", "筛选… (Ctrl+F)"], ["Filtrer…", "Filter…", "Фильтр…", "筛选…"],
    ["Ne montrer qu'un type de carte", "Show only one map type", "Показать только один тип карты", "仅显示一种贴图类型"],
    ["Créature de la forêt", "Forest creature", "Лесное существо", "森林生物"], ["Nom ou chemin", "Name or path", "Имя или путь", "名称或路径"],
    ["Aperçu limité du modèle source", "Limited source model preview", "Ограниченный предпросмотр исходной модели", "源模型有限预览"],
    ["Aperçu limité de la copie modifiée", "Limited modified copy preview", "Ограниченный предпросмотр изменённой копии", "修改副本的有限预览"],
    ["Fermer (Échap)", "Close (Esc)", "Закрыть (Esc)", "关闭 (Esc)"],
    ["Suspendre les vignettes visibles ; les aperçus détaillés restent disponibles", "Pause visible thumbnails; detailed previews remain available", "Приостановить видимые миниатюры; подробный предпросмотр останется доступен", "暂停可见缩略图；详细预览仍可使用"],
    ["Taper une commande, un modèle, une texture…", "Type a command, model, texture…", "Введите команду, модель, текстуру…", "输入命令、模型或纹理…"],
    ["Fermer", "Close", "Закрыть", "关闭"],
    // Frequently inserted UI text is translated as it is rendered.
    ["Chargement du modèle…", "Loading model…", "Загрузка модели…", "正在加载模型…"], ["Modèle indisponible", "Model unavailable", "Модель недоступна", "模型不可用"],
    ["Chargement de la géométrie complète…", "Loading complete geometry…", "Загрузка полной геометрии…", "正在加载完整几何体…"],
    ["Recentrer", "Reset view", "Центрировать", "重置视图"], ["Faces plates", "Flat faces", "Плоские грани", "平面着色"],
    ["Fil de fer", "Wireframe", "Каркас", "线框"], ["Texturé", "Textured", "С текстурами", "纹理"], ["Rendu", "Rendering", "Рендеринг", "渲染"],
    ["Résolution native", "Native resolution", "Исходное разрешение", "原生分辨率"], ["FSR 1 expérimental", "Experimental FSR 1", "Экспериментальный FSR 1", "实验性 FSR 1"],
    ["Fichier d'animation", "Animation file", "Файл анимации", "动画文件"], ["Pose statique", "Static pose", "Статичная поза", "静态姿势"],
    ["Choisir un fichier", "Choose a file", "Выберите файл", "选择文件"], ["Clip", "Clip", "Клип", "片段"],
    ["Pause", "Pause", "Пауза", "暂停"], ["Lire", "Play", "Воспроизвести", "播放"], ["Boucle", "Loop", "Цикл", "循环"],
    ["Images", "Frames", "Кадры", "帧"], ["Image d'animation", "Animation frame", "Кадр анимации", "动画帧"],
    ["Chargement du clip et du squelette…", "Loading clip and skeleton…", "Загрузка клипа и скелета…", "正在加载片段和骨架…"],
    ["Recherche des clips…", "Finding clips…", "Поиск клипов…", "正在查找片段…"], ["Pose statique.", "Static pose.", "Статичная поза.", "静态姿势。"],
    ["Chargement des clips…", "Loading clips…", "Загрузка клипов…", "正在加载片段…"], ["Animation indisponible", "Animation unavailable", "Анимация недоступна", "动画不可用"],
    ["Modèle à associer", "Model to associate", "Связываемая модель", "要关联的模型"], ["Choisir un modèle…", "Choose a model…", "Выберите модель…", "选择模型…"],
    ["Ouvrir le modèle et lire", "Open model and play", "Открыть модель и воспроизвести", "打开模型并播放"],
    ["Exporter les courbes JSON", "Export curves as JSON", "Экспорт кривых в JSON", "将曲线导出为 JSON"],
    ["Maillages", "Meshes", "Сетки", "网格"], ["Faces", "Faces", "Грани", "面"], ["Sommets", "Vertices", "Вершины", "顶点"],
    ["Dimensions", "Dimensions", "Размеры", "尺寸"], ["Matériaux", "Materials", "Материалы", "材质"], ["Budget", "Budget", "Бюджет", "预算"],
    ["Vérifier les UV", "Check UVs", "Проверить UV", "检查 UV"], ["Exporter en GLB / glTF / OBJ", "Export GLB / glTF / OBJ", "Экспорт GLB / glTF / OBJ", "导出 GLB / glTF / OBJ"],
    ["Le clip sélectionné est exporté dans GLB/glTF si son squelette est compatible. Sans clip, l'export est statique. OBJ/MTL restent toujours statiques.", "The selected clip is exported to GLB/glTF if its skeleton is compatible. Without a clip the export is static. OBJ/MTL are always static.", "Выбранный клип экспортируется в GLB/glTF при совместимом скелете. Без клипа экспорт статичен. OBJ/MTL всегда статичны.", "如果骨架兼容，所选片段将导出到 GLB/glTF。未选择片段时导出为静态模型。OBJ/MTL 始终为静态。"],
    ["Les fichiers d'animation du jeu restent inchangés.", "Game animation files remain unchanged.", "Файлы анимации игры не изменяются.", "游戏动画文件保持不变。"],
    ["Choisis un clip compatible ou reviens à la pose statique.", "Choose a compatible clip or return to the static pose.", "Выберите совместимый клип или вернитесь к статичной позе.", "请选择兼容的片段或返回静态姿势。"],
    ["Planche PNG", "PNG sheet", "Лист PNG", "PNG 预览图"], ["Importer un modèle…", "Import a model…", "Импорт модели…", "导入模型…"],
    ["Ouverture du son…", "Opening sound…", "Открытие звука…", "正在打开声音…"], ["Son indisponible", "Sound unavailable", "Звук недоступен", "声音不可用"],
    ["Format source", "Source format", "Формат источника", "源格式"], ["Canaux", "Channels", "Каналы", "声道"], ["Fréquence", "Sample rate", "Частота", "采样率"], ["Durée", "Duration", "Длительность", "时长"],
    ["aucune repérée", "none found", "не найдено", "未找到"], ["Remplacer le son…", "Replace sound…", "Заменить звук…", "替换声音…"],
    ["Réglages enregistrés", "Settings saved", "Настройки сохранены", "设置已保存"], ["Aucune animation repérée dans ce dossier.", "No animations found in this folder.", "В этой папке анимации не найдены.", "此文件夹中未找到动画。"],
    ["Aucun fichier BWAV repéré dans ce dossier.", "No BWAV files found in this folder.", "В этой папке файлы BWAV не найдены.", "此文件夹中未找到 BWAV 文件。"],
    ["Aucune texture repérée dans ce dossier.", "No textures found in this folder.", "В этой папке текстуры не найдены.", "此文件夹中未找到纹理。"],
    ["textures repérées", "textures found", "найдено текстур", "找到的纹理"], ["modèles repérés", "models found", "найдено моделей", "找到的模型"],
    ["BWAV repérés", "BWAV files found", "найдено файлов BWAV", "找到的 BWAV 文件"], ["fichiers modifiés", "modified files", "изменённых файлов", "已修改文件"],
    ["Fichiers modifiés", "Modified files", "Изменённые файлы", "已修改文件"], ["Copies modifiées", "Modified copies", "Изменённые копии", "已修改的副本"],
    ["Fiches de mobs préparées", "Prepared mob records", "Подготовленные записи мобов", "已准备的生物条目"], ["aucun", "none", "нет", "无"],
    ["écrits dans", "written to", "записаны в", "写入"], ["aperçu désactivé", "preview disabled", "предпросмотр отключён", "预览已禁用"],
    ["Aucune fiche pour l'instant.", "No records yet.", "Записей пока нет.", "暂无条目。"],
    ["Rendu des aperçus", "Preview rendering", "Рендеринг предпросмотра", "预览渲染"], ["Filtre des triangles BFRES 10", "BFRES 10 triangle filter", "Фильтр треугольников BFRES 10", "BFRES 10 三角形过滤器"],
    ["Disposition des textures", "Texture layout", "Размещение текстур", "纹理布局"], ["Décodage BCn", "BCn decoding", "Декодирование BCn", "BCn 解码"],
    ["Décompression Zstandard", "Zstandard decompression", "Распаковка Zstandard", "Zstandard 解压"], ["Mémoire du processus", "Process memory", "Память процесса", "进程内存"],
    ["Cache lu / écrit", "Cache read / written", "Кэш прочитан / записан", "缓存读取 / 写入"], ["Maillages en mémoire", "Meshes in memory", "Сетки в памяти", "内存中的网格"],
    ["Remplissage du cache", "Cache usage", "Использование кэша", "缓存使用量"], ["faces arrière + hors écran", "back faces + off-screen", "обратные грани + вне экрана", "背面 + 屏幕外"],
    ["Anticrénelage adaptatif", "Adaptive antialiasing", "Адаптивное сглаживание", "自适应抗锯齿"],
    ["toutes les faces décodées ou refus", "all decoded faces or refusal", "все декодированные грани или отказ", "保留所有已解码面，否则拒绝"],
    ["avec les PNG", "with PNGs", "с PNG", "包含 PNG"], ["géométrie seule", "geometry only", "только геометрия", "仅几何体"],
    ["Glisser pour tourner · molette pour zoomer · flèches et +/− au clavier · Échap quitte le plein écran", "Drag to orbit · wheel to zoom · arrows and +/− on the keyboard · Esc exits fullscreen", "Перетаскивайте для вращения · колесо для масштаба · стрелки и +/− на клавиатуре · Esc выходит из полного экрана", "拖动旋转 · 滚轮缩放 · 使用方向键和 +/− · Esc 退出全屏"],
    ["Matériaux du jeu non reproduits. FSR n'altère pas les faces ; l'animation est une prévisualisation expérimentale.", "Game materials are not reproduced. FSR does not change faces; animation is an experimental preview.", "Материалы игры не воспроизводятся. FSR не изменяет грани; анимация — экспериментальный предпросмотр.", "未重现游戏材质。FSR 不会更改面；动画预览为实验性功能。"],
    ["30 images/s par convention ; rig, matériaux et physique non validés en jeu.", "30 fps by convention; rig, materials and physics are not validated against the game.", "30 кадров/с по соглашению; риг, материалы и физика не проверены в игре.", "按惯例使用 30 帧/秒；骨架、材质和物理效果尚未在游戏中验证。"],
    ["Les animations restent des fichiers séparés du jeu : elles ne sont pas encore injectées dans le modèle exporté.", "Animations remain separate game files: they are not injected into the exported model.", "Анимации остаются отдельными игровыми файлами: они не внедряются в экспортированную модель.", "动画仍是独立的游戏文件：不会注入导出的模型。"],
    ["Association par nom proposée à titre indicatif. Ouvre un modèle, sélectionne le clip et exporte-le en GLB/glTF si compatible. OBJ/MTL restent statiques et les fichiers du jeu ne sont pas modifiés.", "Name-based matching is only a suggestion. Open a model, select the clip and export it to GLB/glTF if compatible. OBJ/MTL remain static and game files are unchanged.", "Сопоставление по имени является лишь подсказкой. Откройте модель, выберите клип и экспортируйте его в GLB/glTF, если он совместим. OBJ/MTL статичны, файлы игры не изменяются.", "按名称匹配仅供参考。打开模型、选择片段，并在兼容时导出为 GLB/glTF。OBJ/MTL 保持静态，游戏文件不会被修改。"],
    ["Aperçu PNG complet : toutes les faces dans le budget.", "Complete PNG preview: all faces fit the budget.", "Полный предпросмотр PNG: все грани помещаются в бюджет.", "完整 PNG 预览：所有面均在预算内。"],
    ["BFRES 10.x : FMDL et FVTX référencés ; tables de formes.", "BFRES 10.x: referenced FMDL and FVTX; shape tables.", "BFRES 10.x: ссылки на FMDL и FVTX; таблицы форм.", "BFRES 10.x：已引用 FMDL 和 FVTX；形状表。"],
    ["Tables de formes et indices LOD 0 lus directement ; autres LOD non exportés.", "Shape tables and LOD 0 indices read directly; other LODs are not exported.", "Таблицы форм и индексы LOD 0 прочитаны напрямую; другие LOD не экспортируются.", "直接读取形状表和 LOD 0 索引；其他 LOD 不会导出。"],
    ["Décodage géométrique seul : pose statique non garantie ; squelette et animation à associer séparément.", "Geometry decoding alone: the static pose is not guaranteed; skeleton and animation must be associated separately.", "Только декодирование геометрии: статичная поза не гарантирована; скелет и анимацию нужно связать отдельно.", "仅解码几何体：不保证静态姿势的准确性；骨架和动画需要单独关联。"],
    ["Opus est exporté sans réencodage. WAV, MP3, FLAC et Ogg sont décodés puis convertis ; les marqueurs de boucle ne sont pas conservés dans ces exports.", "Opus is exported without re-encoding. WAV, MP3, FLAC and Ogg are decoded and converted; loop markers are not retained in these exports.", "Opus экспортируется без перекодирования. WAV, MP3, FLAC и Ogg декодируются и конвертируются; маркеры цикла в этих экспортированных файлах не сохраняются.", "Opus 导出时不会重新编码。WAV、MP3、FLAC 和 Ogg 会先解码再转换；这些导出文件不保留循环标记。"],
    ["Moteur Rust non compilé ; lancer python tools/build_rust.py. · Décompression et compression par le module zstandard.", "Rust engine not built; run python tools/build_rust.py. · Compression and decompression use the zstandard module.", "Движок Rust не собран; запустите python tools/build_rust.py. · Сжатие и распаковка выполняются модулем zstandard.", "Rust 引擎尚未构建；请运行 python tools/build_rust.py。 · 压缩和解压使用 zstandard 模块。"],
    ["Vidéos WebM (expérimental)", "WebM videos (experimental)", "Видео WebM (экспериментально)", "WebM 视频（实验性）"],
    ["Lecture WebM selon les codecs du navigateur. Le remplacement copie un WebM fourni sans transcodage ; la compatibilité en jeu et les données NDVE associées ne sont pas vérifiées ou modifiées.", "WebM playback depends on browser codecs. Replacement copies a supplied WebM without transcoding; in-game compatibility and associated NDVE data are not checked or changed.", "Воспроизведение WebM зависит от кодеков браузера. Замена копирует предоставленный WebM без перекодирования; совместимость с игрой и связанные данные NDVE не проверяются и не изменяются.", "WebM 播放取决于浏览器编解码器。替换时会原样复制提供的 WebM；不会检查或修改游戏内兼容性和关联的 NDVE 数据。"],
    ["Aucune vidéo WebM repérée dans ce dossier.", "No WebM videos found in this folder.", "В этой папке видео WebM не найдены.", "此文件夹中未找到 WebM 视频。"],
    ["Ouverture de la vidéo…", "Opening video…", "Открытие видео…", "正在打开视频…"], ["Vidéo indisponible", "Video unavailable", "Видео недоступно", "视频不可用"],
    ["Conteneur WebM vérifié ; lecture selon les codecs du navigateur.", "WebM container checked; playback depends on browser codecs.", "Контейнер WebM проверен; воспроизведение зависит от кодеков браузера.", "已检查 WebM 容器；播放取决于浏览器编解码器。"],
    ["Télécharger WebM", "Download WebM", "Скачать WebM", "下载 WebM"], ["Remplacer la vidéo…", "Replace video…", "Заменить видео…", "替换视频…"],
    ["copie de travail", "working copy", "рабочая копия", "工作副本"], ["fichier source", "source file", "исходный файл", "源文件"],
    ["Copie WebM sans transcodage (32 Mio maximum). Seul l'en-tête du conteneur est vérifié ; codecs, durée, résolution et compatibilité en jeu ne sont pas garantis. Les données NDVE, audio séparé et RESTBL ne sont pas mis à jour.", "WebM copy without transcoding (32 MiB maximum). Only the container header is checked; codecs, duration, resolution and in-game compatibility are not guaranteed. NDVE data, separate audio and RESTBL are not updated.", "Копия WebM без перекодирования (не более 32 МиБ). Проверяется только заголовок контейнера; кодеки, длительность, разрешение и совместимость с игрой не гарантируются. Данные NDVE, отдельное аудио и RESTBL не обновляются.", "原样复制 WebM，不进行转码（最多 32 MiB）。仅检查容器头；不保证编解码器、时长、分辨率或游戏内兼容性。不会更新 NDVE 数据、独立音频或 RESTBL。"],
    ["Lecture impossible : ce navigateur ne prend peut-être pas en charge les codecs de cette vidéo.", "Playback failed: this browser may not support the video's codecs.", "Воспроизведение невозможно: браузер может не поддерживать кодеки этого видео.", "无法播放：此浏览器可能不支持该视频的编解码器。"],
    ["Remplacer la copie de travail ? Aucun transcodage ni vérification en jeu ; NDVE et RESTBL ne seront pas mis à jour.", "Replace the working copy? No transcoding or in-game validation is performed; NDVE and RESTBL will not be updated.", "Заменить рабочую копию? Перекодирование и проверка в игре не выполняются; NDVE и RESTBL не будут обновлены.", "替换工作副本？不会进行转码或游戏内验证；NDVE 和 RESTBL 不会更新。"],
    ["Remplacer la copie de travail ? Le son sera converti en BWAV PCM16 expérimental. BARS et RESTBL ne seront pas mis à jour ; la compatibilité en jeu n'est pas garantie.", "Replace the working copy? The sound will be converted to experimental PCM16 BWAV. BARS and RESTBL will not be updated; in-game compatibility is not guaranteed.", "Заменить рабочую копию? Звук будет преобразован в экспериментальный BWAV PCM16. BARS и RESTBL не будут обновлены; совместимость с игрой не гарантируется.", "替换工作副本？声音将转换为实验性 PCM16 BWAV。BARS 和 RESTBL 不会更新；不保证游戏内兼容性。"],
    ["Assembler ce mod ? Le nom du dossier est nettoyé : seuls les lettres, chiffres, tirets et _ sont conservés (ou HookyMod s'il ne reste rien). Si ce dossier existe déjà dans mods/, il sera entièrement remplacé, sans sauvegarde.", "Build this mod?\n\nThe folder name is sanitized: only letters, numbers, hyphens and _ are kept (or HookyMod if nothing remains). If this folder already exists in mods/, it will be completely replaced without a backup.", "Собрать этот мод?\n\nВ имени папки остаются только буквы, цифры, дефисы и _ (или HookyMod, если ничего не осталось). Если папка уже существует в mods/, она будет полностью заменена без резервной копии.", "组装此模组？\n\n文件夹名称只保留字母、数字、连字符和 _（若为空则使用 HookyMod）。如果 mods/ 中已存在该文件夹，将完全替换且不备份。"],
    ["Quitter l'atelier ? Le serveur local va s'arrêter.", "Quit the workshop? The local server will stop.", "Выйти из мастерской? Локальный сервер остановится.", "退出工作区？本地服务器将停止。"],
    ["La session ou les réglages n'ont pas pu être enregistrés. Quitter quand même ?", "The session or settings could not be saved. Quit anyway?", "Не удалось сохранить сеанс или настройки. Всё равно выйти?", "无法保存会话或设置。仍要退出吗？"],
    ["Fichier audio trop volumineux (24 Mio maximum).", "Audio file is too large (24 MiB maximum).", "Аудиофайл слишком велик (не более 24 МиБ).", "音频文件过大（最多 24 MiB）。"],
    ["Fichier vidéo trop volumineux (32 Mio maximum).", "Video file is too large (32 MiB maximum).", "Видеофайл слишком велик (не более 32 МиБ).", "视频文件过大（最多 32 MiB）。"],
    ["Choisis un modèle et un clip.", "Choose a model and a clip.", "Выберите модель и клип.", "选择模型和片段。"],
    ["PNG indisponible", "PNG unavailable", "PNG недоступен", "PNG 不可用"],
    ["Ouvre d'abord la vue 3D.", "Open the 3D view first.", "Сначала откройте 3D-вид.", "请先打开 3D 视图。"],
    ["⏸ Pause vignettes", "⏸ Pause thumbnails", "⏸ Приостановить миниатюры", "⏸ 暂停缩略图"],
    ["▶ Reprendre vignettes", "▶ Resume thumbnails", "▶ Возобновить миниатюры", "▶ 恢复缩略图"],
    ["Téléchargement terminé", "Download complete", "Загрузка завершена", "下载完成"],
    ["Téléchargement en cours", "Downloading", "Загрузка", "正在下载"],
    ["Téléchargement impossible", "Download failed", "Ошибка загрузки", "下载失败"],
    ["Rafraîchir le projet", "Refresh project", "Обновить проект", "刷新项目"],
    ["Rescanner les fichiers", "Rescan files", "Пересканировать файлы", "重新扫描文件"],
    ["Exporter la sélection (ZIP)", "Export selection (ZIP)", "Экспорт выбора (ZIP)", "导出所选内容（ZIP）"],
    ["Exporter tout le projet", "Export entire project", "Экспортировать весь проект", "导出整个项目"],
    ["Assembler le mod…", "Build the mod…", "Собрать мод…", "组装模组…"],
    ["Ouvrir le dossier de travail", "Open work folder", "Открыть рабочую папку", "打开工作文件夹"],
    ["Quitter l'atelier", "Quit workshop", "Выйти из мастерской", "退出工作区"],
    ["Palette de commandes", "Command palette", "Палитра команд", "命令面板"],
    ["Chercher dans la vue", "Search this view", "Поиск в текущем виде", "在当前视图中搜索"],
    ["Vider le journal", "Clear log", "Очистить журнал", "清空日志"],
    ["Vue des modèles", "Models view", "Модели", "模型视图"],
    ["Vue des ensembles (modèle + textures + animations)", "Collections view (model + textures + animations)", "Наборы (модель + текстуры + анимации)", "资源集视图（模型 + 纹理 + 动画）"],
    ["Exporter le modèle sélectionné en GLB / glTF / OBJ", "Export selected model as GLB / glTF / OBJ", "Экспортировать выбранную модель в GLB / glTF / OBJ", "将所选模型导出为 GLB / glTF / OBJ"],
    ["Vue des textures", "Textures view", "Текстуры", "纹理视图"],
    ["Exporter la texture sélectionnée en PNG", "Export selected texture as PNG", "Экспортировать выбранную текстуру в PNG", "将所选纹理导出为 PNG"],
    ["Analyser les fichiers choisis", "Analyze selected files", "Проанализировать выбранные файлы", "分析所选文件"],
    ["Masquer l'inspecteur", "Hide inspector", "Скрыть инспектор", "隐藏检查器"],
    ["Densité compacte", "Compact density", "Компактная плотность", "紧凑布局"],
    ["Réduire l'interface", "Minimize interface", "Свернуть интерфейс", "最小化界面"],
    ["Vider le cache des vignettes", "Clear thumbnail cache", "Очистить кэш миниатюр", "清除缩略图缓存"],
    ["Raccourcis clavier", "Keyboard shortcuts", "Горячие клавиши", "键盘快捷键"],
    ["À propos", "About", "О программе", "关于"],
    ["Aller à l'accueil", "Go home", "На главную", "返回主页"],
    ["Aller aux modèles", "Go to models", "Перейти к моделям", "前往模型"],
    ["Aller aux animations", "Go to animations", "Перейти к анимациям", "前往动画"],
    ["Aller aux textures", "Go to textures", "Перейти к текстурам", "前往纹理"],
    ["Aller aux sons", "Go to sounds", "Перейти к звукам", "前往声音"],
    ["Aller aux vidéos", "Go to videos", "Перейти к видео", "前往视频"],
    ["Aller aux mobs", "Go to mobs", "Перейти к мобам", "前往生物"],
    ["Aller aux réglages", "Go to settings", "Перейти к настройкам", "前往设置"],
    ["Injecter le modèle source", "Inject source model", "Внедрить исходную модель", "注入源模型"],
    ["Basculer le journal", "Toggle log", "Показать или скрыть журнал", "切换日志"],
    ["Réinitialiser les réglages", "Reset settings", "Сбросить настройки", "重置设置"],
    ["Rescanner les fichiers du projet", "Rescan project files", "Пересканировать файлы проекта", "重新扫描项目文件"],
    ["Tout sélectionner", "Select all", "Выбрать всё", "全选"],
    ["Vider la sélection", "Clear selection", "Очистить выбор", "清空选择"],
    ["aucun résultat", "no results", "ничего не найдено", "没有结果"],
    ["Échap", "Esc", "Esc", "Esc"],
    ["vue", "view", "вид", "视图"], ["action", "action", "действие", "操作"],
    ["affichage", "display", "отображение", "显示"], ["sélection", "selection", "выбор", "选择"],
    ["mémoire", "memory", "память", "内存"], ["fenêtre", "window", "окно", "窗口"],
    ["modèle", "model", "модель", "模型"], ["texture", "texture", "текстура", "纹理"],
    ["import", "import", "импорт", "导入"],
    ["BFRES auxiliaires", "auxiliary BFRES", "вспомогательных BFRES", "个辅助 BFRES"],
    ["fiches de mobs", "mob records", "записей мобов", "个生物条目"],
    ["Racine", "Root", "Корневая папка", "根目录"],
    ["Dossier de travail", "Work folder", "Рабочая папка", "工作文件夹"],
    ["aucune", "none", "нет", "无"],
    ["(aucun modèle vérifié)", "(no verified model)", "(нет проверенной модели)", "（没有已验证的模型）"],
    ["(aucun modèle réinjectable vérifié)", "(no verified injectable model)", "(нет проверенной модели для внедрения)", "（没有已验证的可注入模型）"],
    ["(choisir un modèle cible)", "(choose a target model)", "(выберите целевую модель)", "（选择目标模型）"],
    ["Moteur Rust", "Rust engine", "Движок Rust", "Rust 引擎"],
    ["actif", "active", "активен", "已启用"], ["inactif", "inactive", "неактивен", "未启用"],
    ["inconnu", "unknown", "неизвестно", "未知"], ["inconnue", "unknown", "неизвестно", "未知"],
    ["Python (repli)", "Python (fallback)", "Python (резервный вариант)", "Python（回退）"],
    ["Rust si compatible, sinon Python", "Rust if compatible, otherwise Python", "Rust при совместимости, иначе Python", "兼容时使用 Rust，否则使用 Python"],
    ["Rust si compatible, sinon zstandard", "Rust if compatible, otherwise zstandard", "Rust при совместимости, иначе zstandard", "兼容时使用 Rust，否则使用 zstandard"],
    ["C (texture2ddecoder ; BC2 en Python)", "C (texture2ddecoder; BC2 in Python)", "C (texture2ddecoder; BC2 на Python)", "C（texture2ddecoder；BC2 使用 Python）"],
    ["Rust (BC1 à BC5)", "Rust (BC1 to BC5)", "Rust (BC1–BC5)", "Rust（BC1 至 BC5）"],
    ["Moteur natif chargé.", "Native engine loaded.", "Нативный движок загружен.", "已加载原生引擎。"],
    ["Tables de formes BFRES 10", "BFRES 10 shape tables", "Таблицы форм BFRES 10", "BFRES 10 形状表"],
    ["Indices et positions BFRES 10", "BFRES 10 indices and positions", "Индексы и позиции BFRES 10", "BFRES 10 索引和位置"],
    ["Encodage BNTX pris en charge", "Supported BNTX encoding", "Поддерживаемое кодирование BNTX", "支持的 BNTX 编码"],
    ["Lecture et reconstruction SARC", "SARC reading and rebuilding", "Чтение и сборка SARC", "SARC 读取和重建"],
    ["Lecture et reconstruction RESTBL", "RESTBL reading and rebuilding", "Чтение и сборка RESTBL", "RESTBL 读取和重建"],
    ["Vignettes sur disque", "Thumbnails on disk", "Миниатюры на диске", "磁盘上的缩略图"],
    ["Dernier scan", "Last scan", "Последнее сканирование", "上次扫描"],
    ["parent :", "parent:", "родитель:", "父项："], ["parent", "parent", "родитель", "父项"],
  ];
  const dict = new Map(rows.map(([fr, en, ru, zh]) => [fr, { en, ru, zh }]));
  let language = "fr";
  const textState = new WeakMap(), attrState = new WeakMap();
  const attrs = ["title", "placeholder", "aria-label", "alt"];
  const normalize = (s) => s.replace(/\s+/g, " ").trim();
  function lookup(source) {
    if (language === "fr") return source;
    const match = dict.get(normalize(source));
    if (match) return match[language];
    let m = normalize(source).match(/^Afficher (\d+) de plus \((\d+) restant\(s\)\)$/);
    if (m) return { en: `Show ${m[1]} more (${m[2]} remaining)`, ru: `Показать ещё ${m[1]} (осталось ${m[2]})`, zh: `再显示 ${m[1]} 项（剩余 ${m[2]} 项）` }[language];
    m = normalize(source).match(/^(\d+) vidéo\(s\) WebM repérée\(s\)$/);
    if (m) return { en: `${m[1]} WebM video(s) found`, ru: `Найдено видео WebM: ${m[1]}`, zh: `找到 ${m[1]} 个 WebM 视频` }[language];
    m = normalize(source).match(/^(\d+) fichier\(s\) d'animation repéré\(s\) · cliquer pour inspecter$/);
    if (m) return { en: `${m[1]} animation file(s) found · click to inspect`, ru: `Найдено файлов анимации: ${m[1]} · нажмите для просмотра`, zh: `找到 ${m[1]} 个动画文件 · 点击查看` }[language];
    m = normalize(source).match(/^(\d+) clip\(s\) squelettique\(s\), (\d+) en rotations Euler prises en charge\.$/);
    if (m) return { en: `${m[1]} skeletal clip(s), ${m[2]} with supported Euler rotations.`, ru: `Скелетных клипов: ${m[1]}; с поддерживаемыми вращениями Эйлера: ${m[2]}.`, zh: `${m[1]} 个骨骼片段，其中 ${m[2]} 个使用受支持的欧拉旋转。` }[language];
    m = source.trim().match(/^([\s\S]+) · (\d+) images$/);
    if (m) return { en: `${m[1]} · ${m[2]} frames`, ru: `${m[1]} · кадров: ${m[2]}`, zh: `${m[1]} · ${m[2]} 帧` }[language];
    m = normalize(source).match(/^(\d+) os associés(?:, (\d+) absents)? · 30 images\/s par convention · lecture expérimentale, non validée en jeu\.$/);
    if (m) return { en: `${m[1]} bones matched${m[2] ? `, ${m[2]} missing` : ""} · 30 fps by convention · experimental playback, not validated in game.`, ru: `Связано костей: ${m[1]}${m[2] ? `, отсутствует: ${m[2]}` : ""} · 30 кадров/с по соглашению · экспериментальный предпросмотр, не проверен в игре.`, zh: `已关联 ${m[1]} 根骨骼${m[2] ? `，缺少 ${m[2]} 根` : ""} · 按惯例使用 30 帧/秒 · 实验性播放，未经游戏内验证。` }[language];
    m = normalize(source).match(/^([\d.,]+ (?:B|KiB|MiB|GiB)) · (copie de travail|fichier source)$/);
    if (m) return { en: `${m[1]} · ${m[2] === "copie de travail" ? "working copy" : "source file"}`, ru: `${m[1]} · ${m[2] === "copie de travail" ? "рабочая копия" : "исходный файл"}`, zh: `${m[1]} · ${m[2] === "copie de travail" ? "工作副本" : "源文件"}` }[language];
    m = normalize(source).match(/^([\d.,]+ (?:B|KiB|MiB|GiB)) estimés sur ([\d.,]+ (?:B|KiB|MiB|GiB)) \((\d+) %\) · PNG$/);
    if (m) return { en: `${m[1]} estimated of ${m[2]} (${m[3]}%) · PNG`, ru: `Оценка: ${m[1]} из ${m[2]} (${m[3]}%) · PNG`, zh: `预计 ${m[1]} / ${m[2]}（${m[3]}%）· PNG` }[language];
    m = normalize(source).match(/^([\d.,]+ (?:B|KiB|MiB|GiB)) · (\d+) (fichier|modèle)\(s\)$/);
    if (m) return { en: `${m[1]} · ${m[2]} ${m[3] === "fichier" ? "file(s)" : "model(s)"}`, ru: `${m[1]} · ${m[2]} ${m[3] === "fichier" ? "файлов" : "моделей"}`, zh: `${m[1]} · ${m[2]} ${m[3] === "fichier" ? "个文件" : "个模型"}` }[language];
    m = normalize(source).match(/^(\d+) → (\d+) échantillons$/);
    if (m) return { en: `${m[1]} → ${m[2]} samples`, ru: `${m[1]} → ${m[2]} сэмплов`, zh: `${m[1]} → ${m[2]} 个采样` }[language];
    m = source.trim().match(/^Copie (audio|vidéo) enregistrée → ([\s\S]+)$/);
    if (m) return { en: `${m[1] === "audio" ? "Audio" : "Video"} working copy saved → ${m[2]}`, ru: `${m[1] === "audio" ? "Аудио" : "Видео"}: рабочая копия сохранена → ${m[2]}`, zh: `${m[1] === "audio" ? "音频" : "视频"}工作副本已保存 → ${m[2]}` }[language];
    m = source.trim().match(/^Projet ouvert : ([\s\S]+)$/);
    if (m) return { en: `Project opened: ${m[1]}`, ru: `Проект открыт: ${m[1]}`, zh: `项目已打开：${m[1]}` }[language];
    m = normalize(source).match(/^(\d+) en attente d'assemblage$/);
    if (m) return { en: `${m[1]} awaiting build`, ru: `Ожидают сборки: ${m[1]}`, zh: `${m[1]} 项待组装` }[language];
    m = source.trim().match(/^Ouvrir (le modèle|la texture) ([\s\S]+)$/);
    if (m) return { en: `Open ${m[1] === "le modèle" ? "model" : "texture"} ${m[2]}`, ru: `Открыть ${m[1] === "le modèle" ? "модель" : "текстуру"} ${m[2]}`, zh: `打开${m[1] === "le modèle" ? "模型" : "纹理"} ${m[2]}` }[language];
    m = source.trim().match(/^Ouvrir ([\s\S]+)$/);
    if (m) return { en: `Open ${m[1]}`, ru: `Открыть ${m[1]}`, zh: `打开 ${m[1]}` }[language];
    m = normalize(source).match(/^(\d+) modèles repérés · (\d+) ressources auxiliaires · (\d+) textures repérées · (\d+) fiches de mobs$/);
    if (m) return { en: `${m[1]} models found · ${m[2]} auxiliary assets · ${m[3]} textures found · ${m[4]} mob records`, ru: `Моделей: ${m[1]} · вспомогательных ресурсов: ${m[2]} · текстур: ${m[3]} · записей мобов: ${m[4]}`, zh: `找到 ${m[1]} 个模型 · ${m[2]} 个辅助资源 · ${m[3]} 个纹理 · ${m[4]} 个生物条目` }[language];
    m = normalize(source).match(/^(\d+) fichier\(s\) repéré\(s\) · (\d+) dictionnaire\(s\) zstd$/);
    if (m) return { en: `${m[1]} files found · ${m[2]} zstd dictionaries`, ru: `Найдено файлов: ${m[1]} · словарей zstd: ${m[2]}`, zh: `找到 ${m[1]} 个文件 · ${m[2]} 个 zstd 字典` }[language];
    m = normalize(source).match(/^(\d+) fichier\(s\) repéré\(s\) · (sans dictionnaire zstd|zstandard absent)$/);
    if (m) return { en: `${m[1]} files found · ${m[2] === "zstandard absent" ? "zstandard missing" : "no zstd dictionary"}`, ru: `Найдено файлов: ${m[1]} · ${m[2] === "zstandard absent" ? "zstandard отсутствует" : "нет словаря zstd"}`, zh: `找到 ${m[1]} 个文件 · ${m[2] === "zstandard absent" ? "未安装 zstandard" : "没有 zstd 字典"}` }[language];
    m = normalize(source).match(/^(\d+) (modèle|texture|son|ensemble)\(s\) vérifié\(s\)(?: · (\d+) en vérification)?(?: · (\d+) non pris en charge masqué\(s\))?(?: · (\d+) vérification\(s\) à réessayer)?$/);
    if (m) {
      const kinds = { modèle: ["model(s)", "моделей", "个模型"], texture: ["texture(s)", "текстур", "个纹理"], son: ["sound(s)", "звуков", "个声音"], ensemble: ["collection(s)", "наборов", "个资源集"] }[m[2]];
      const parts = {
        en: [`${m[1]} ${kinds[0]} verified`, m[3] && `${m[3]} being checked`, m[4] && `${m[4]} unsupported hidden`, m[5] && `${m[5]} check(s) to retry`],
        ru: [`Проверено ${kinds[1]}: ${m[1]}`, m[3] && `Проверяется: ${m[3]}`, m[4] && `Скрыто неподдерживаемых: ${m[4]}`, m[5] && `Повторить проверок: ${m[5]}`],
        zh: [`已验证 ${m[1]} ${kinds[2]}`, m[3] && `正在检查 ${m[3]} 项`, m[4] && `已隐藏 ${m[4]} 项不支持的资源`, m[5] && `${m[5]} 项需要重新检查`],
      };
      return parts[language].filter(Boolean).join(" · ");
    }
    m = normalize(source).match(/^(\d+) (fichier|modèle)\(s\)$/);
    if (m) return { en: `${m[1]} ${m[2] === "fichier" ? "file(s)" : "model(s)"}`, ru: `${m[1]} ${m[2] === "fichier" ? "файлов" : "моделей"}`, zh: `${m[1]} ${m[2] === "fichier" ? "个文件" : "个模型"}` }[language];
    m = normalize(source).match(/^(.+) · compressé$/);
    if (m) return { en: `${m[1]} · compressed`, ru: `${m[1]} · сжат`, zh: `${m[1]} · 已压缩` }[language];
    m = normalize(source).match(/^(\d+) remarque\(s\) de décodage$/);
    if (m) return { en: `${m[1]} decoding note(s)`, ru: `Примечаний декодирования: ${m[1]}`, zh: `${m[1]} 条解码说明` }[language];
    m = normalize(source).match(/^(\d+) animation\(s\) liée\(s\)$/);
    if (m) return { en: `${m[1]} linked animation(s)`, ru: `Связанных анимаций: ${m[1]}`, zh: `${m[1]} 个关联动画` }[language];
    m = source.trim().match(/^Texture référencée ([\s\S]+) : absente ou illisible, non reliée\.$/);
    if (m) return { en: `Referenced texture ${m[1]}: missing or unreadable, not linked.`, ru: `Текстура ${m[1]} отсутствует или нечитаема; не связана.`, zh: `引用的纹理 ${m[1]}：缺失或无法读取，未关联。` }[language];
    m = normalize(source).match(/^(\d+) forme\(s\) rigide\(s\) replacée\(s\) en pose de référence FSKL ; les triangles sont conservés\.$/);
    if (m) return { en: `${m[1]} rigid shape(s) placed in the FSKL rest pose; triangles are retained.`, ru: `Жёстких форм в исходной позе FSKL: ${m[1]}; треугольники сохранены.`, zh: `${m[1]} 个刚性形状已放置到 FSKL 参考姿势；保留所有三角形。` }[language];
    m = normalize(source).match(/^La source doit avoir (\d+) canal\(aux\) à (\d+) Hz \(24 Mio maximum\)\. Le remplacement produit un BWAV PCM16 dans la copie de travail, même si l'original est Opus ou DSP\. BARS, événements et RESTBL ne sont pas modifiés ; fonctionnement en jeu non garanti\.$/);
    if (m) return { en: `The source must have ${m[1]} channel(s) at ${m[2]} Hz (24 MiB maximum). Replacement writes a PCM16 BWAV working copy even if the original is Opus or DSP. BARS, events and RESTBL are not changed; in-game operation is not guaranteed.`, ru: `Источник должен иметь каналов: ${m[1]}, частота: ${m[2]} Гц (не более 24 МиБ). Замена создаёт рабочую копию PCM16 BWAV, даже если оригинал — Opus или DSP. BARS, события и RESTBL не изменяются; работа в игре не гарантируется.`, zh: `源文件必须具有 ${m[1]} 个声道、采样率 ${m[2]} Hz（最多 24 MiB）。即使原文件为 Opus 或 DSP，替换也会生成 PCM16 BWAV 工作副本。不会修改 BARS、事件或 RESTBL；不保证游戏内可用。` }[language];
    m = normalize(source).match(/^([\d\s, ]+) triangles · WebGL2 · résolution native$/);
    if (m) return { en: `${m[1]} triangles · WebGL2 · native resolution`, ru: `Треугольников: ${m[1]} · WebGL2 · исходное разрешение`, zh: `${m[1]} 个三角形 · WebGL2 · 原生分辨率` }[language];
    return source;
  }
  function translated(value) {
    const found = lookup(value);
    if (found === value) return value;
    const m = value.match(/^(\s*)([\s\S]*?)(\s*)$/);
    return m ? m[1] + found + m[3] : found;
  }
  function text(node) {
    if (!node.parentElement || node.parentElement.closest("script,style,textarea,code,[data-no-i18n]")) return;
    const value = node.nodeValue, prior = textState.get(node);
    const source = prior && prior.output === value ? prior.source : value;
    const output = translated(source);
    textState.set(node, { source, output });
    if (value !== output) node.nodeValue = output;
  }
  function element(node) {
    if (node.closest("script,style,textarea,code,[data-no-i18n]")) return;
    let state = attrState.get(node);
    if (!state) { state = {}; attrState.set(node, state); }
    for (const attr of attrs) {
      if (!node.hasAttribute(attr)) continue;
      const value = node.getAttribute(attr), prior = state[attr];
      const source = prior && prior.output === value ? prior.source : value;
      const output = translated(source); state[attr] = { source, output };
      if (value !== output) node.setAttribute(attr, output);
    }
  }
  function apply(root) {
    if (root.nodeType === 3) { text(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    if (root.nodeType === 1) element(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.nodeType === 3) text(node); else element(node);
    }
  }
  function setLanguage(value) {
    const next = ["fr", "en", "ru", "zh"].includes(value) ? value : "fr";
    if (language === next && document.documentElement.lang === next) return;
    language = next; document.documentElement.lang = next; apply(document.body);
    window.dispatchEvent(new CustomEvent("hooky-language-change", { detail: { language } }));
  }
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "characterData") text(record.target);
      else if (record.type === "attributes") element(record.target);
      else for (const node of record.addedNodes) apply(node);
    }
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: attrs });
  return { setLanguage, translate: lookup, get language() { return language; } };
})();
window.HookyI18n = HookyI18n;
