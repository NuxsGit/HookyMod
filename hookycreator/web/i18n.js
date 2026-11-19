"use strict";

/* Offline UI translations. Keys are the original French text, not user asset
   names or paths. Unknown diagnostics remain intact rather than being guessed. */
const HookyI18n = (() => {
  const rows = [
    // Player and FSR controls. Asset names and paths remain untranslated.
    ["Player : personnages, tenues et objets", "Player: characters, outfits and items", "Player: персонажи, одежда и предметы", "Player：角色、服装和物品"],
    ["Associer un personnage, ses tenues et objets pour tester des animations.", "Combine a character, outfits and items to test animations.", "Объедините персонажа, одежду и предметы для проверки анимации.", "组合角色、服装和物品以测试动画。"],
    ["ATELIER / PERSONNAGE", "STUDIO / CHARACTER", "МАСТЕРСКАЯ / ПЕРСОНАЖ", "工作室 / 角色"],
    ["Compose un personnage à partir de tes fichiers : Link enfant ou adulte, tenues et objets, puis teste un clip sur les squelettes compatibles.", "Build a character from your files: child or adult Link, outfits and items, then test a clip on compatible skeletons.", "Соберите персонажа из своих файлов: юный или взрослый Линк, одежда и предметы; затем проверьте клип на совместимых скелетах.", "使用自己的文件组合少年或成年林克、服装和物品，然后在兼容骨架上测试片段。"],
    ["Réglages 3D ↗", "3D settings ↗", "Настройки 3D ↗", "3D 设置 ↗"],
    ["Aperçu du personnage", "Character preview", "Предпросмотр персонажа", "角色预览"],
    ["VUE INTERACTIVE", "INTERACTIVE VIEW", "ИНТЕРАКТИВНЫЙ ВИД", "交互式视图"],
    ["La scène", "The scene", "Сцена", "场景"],
    ["Personnage 3D : glisser pour tourner, molette pour zoomer, flèches et plus ou moins au clavier", "3D character: drag to rotate, scroll to zoom, or use arrow and plus/minus keys", "Персонаж 3D: перетаскивайте для вращения, используйте колесо или клавиши стрелок и плюс/минус", "3D 角色：拖动旋转，滚轮缩放，或使用方向键和加减键"],
    ["Ta scène commence ici", "Your scene starts here", "Ваша сцена начинается здесь", "从这里开始场景"],
    ["Aperçu indisponible", "Preview unavailable", "Предпросмотр недоступен", "预览不可用"],
    ["Aperçu indisponible. Réessaie ou change de modèle.", "Preview unavailable. Retry or choose another model.", "Предпросмотр недоступен. Повторите попытку или выберите другую модель.", "预览不可用。请重试或选择其他模型。"],
    ["Consulte le diagnostic sous la scène.", "See the diagnostic below the scene.", "Посмотрите сообщение под сценой.", "请查看场景下方的诊断信息。"],
    ["Choisis un modèle de base dans ton projet.", "Choose a base model from your project.", "Выберите базовую модель из проекта.", "从项目中选择基础模型。"],
    ["Aucun modèle chargé.", "No model loaded.", "Модель не загружена.", "未加载模型。"],
    ["Afficher le modèle de base", "Show base model", "Показать базовую модель", "显示基础模型"],
    ["Suivre la racine · caméra", "Follow root · camera", "Следовать за корнем · камера", "跟随根骨骼 · 相机"],
    ["Glisser pour tourner · molette pour zoomer · flèches et +/− au clavier. La caméra suit la racine unique si l'option est activée : désactive-la pour voir son déplacement global.", "Drag to rotate · scroll to zoom · arrow and +/− keys. When enabled, the camera follows a single root: turn this off to see its global movement.", "Перетаскивайте для вращения · колесо для масштаба · стрелки и +/− на клавиатуре. Если включено, камера следует за единственной корневой костью; отключите, чтобы видеть её общее движение.", "拖动旋转 · 滚轮缩放 · 方向键及 +/− 键。启用时相机跟随唯一根骨骼；关闭后可看到其全局位移。"],
    ["02 / MOUVEMENT", "02 / MOTION", "02 / ДВИЖЕНИЕ", "02 / 动作"],
    ["Tester une animation", "Test an animation", "Проверить анимацию", "测试动画"],
    ["Rechercher un fichier d'animation", "Search animation files", "Поиск файлов анимации", "搜索动画文件"],
    ["Choisis un fichier d'animation puis un clip pris en charge.", "Choose an animation file and a supported clip.", "Выберите файл анимации и поддерживаемый клип.", "选择动画文件和受支持的片段。"],
    ["01 / PERSONNAGE", "01 / CHARACTER", "01 / ПЕРСОНАЖ", "01 / 角色"],
    ["Modèle de base", "Base model", "Базовая модель", "基础模型"],
    ["Famille suggérée", "Suggested family", "Предполагаемая группа", "推荐类别"],
    ["Link enfant", "Child Link", "Юный Линк", "少年林克"], ["Link adulte", "Adult Link", "Взрослый Линк", "成年林克"],
    ["Tous les modèles du projet", "All project models", "Все модели проекта", "项目中的所有模型"],
    ["Rechercher un modèle", "Search models", "Поиск модели", "搜索模型"],
    ["Personnage", "Character", "Персонаж", "角色"],
    ["Les catégories sont des suggestions par nom.", "Categories are suggestions based on names.", "Категории предлагаются по имени.", "类别根据名称推荐。"],
    ["03 / ÉQUIPEMENT", "03 / EQUIPMENT", "03 / СНАРЯЖЕНИЕ", "03 / 装备"],
    ["Tenues & objets", "Outfits & items", "Одежда и предметы", "服装与物品"],
    ["Emplacement", "Slot", "Ячейка", "装备槽"], ["Tête", "Head", "Голова", "头部"],
    ["Tenue / corps", "Outfit / body", "Одежда / тело", "服装 / 身体"], ["Tenue", "Outfit", "Одежда", "服装"],
    ["Jambes", "Legs", "Ноги", "腿部"], ["Main droite", "Right hand", "Правая рука", "右手"], ["Main gauche", "Left hand", "Левая рука", "左手"],
    ["Objet · main droite", "Item · right hand", "Предмет · правая рука", "物品 · 右手"],
    ["Objet · main gauche", "Item · left hand", "Предмет · левая рука", "物品 · 左手"],
    ["Ressources à proposer", "Resources to suggest", "Предлагаемые ресурсы", "推荐资源"],
    ["Tenues et objets suggérés", "Suggested outfits and items", "Предлагаемые одежда и предметы", "推荐服装和物品"],
    ["Modèle à équiper", "Model to equip", "Модель для оснащения", "要装备的模型"],
    ["Association", "Attachment", "Связь", "关联方式"],
    ["Tenue : animer son squelette", "Outfit: animate its skeleton", "Одежда: анимировать её скелет", "服装：动画化其骨架"],
    ["Objet : attacher à un os", "Item: attach to a bone", "Предмет: прикрепить к кости", "物品：附着到骨骼"],
    ["Modèle statique · espace commun", "Static model · shared space", "Статичная модель · общее пространство", "静态模型 · 共享空间"],
    ["Os d'attache", "Attachment bone", "Кость крепления", "附着骨骼"], ["Choisir un os…", "Choose a bone…", "Выберите кость…", "选择骨骼…"],
    ["Ajuster la position, la rotation et l'échelle", "Adjust position, rotation and scale", "Изменить положение, вращение и масштаб", "调整位置、旋转和缩放"],
    ["Valeurs locales, à ajuster selon le modèle ; aucun point d'attache n'est deviné.", "Local values to adjust for the model; attachment points are not guessed.", "Локальные значения нужно настроить под модель; точки крепления не угадываются.", "根据模型调整局部值；不会猜测附着点。"],
    ["Décalage X", "X offset", "Смещение X", "X 偏移"], ["Décalage Y", "Y offset", "Смещение Y", "Y 偏移"], ["Décalage Z", "Z offset", "Смещение Z", "Z 偏移"],
    ["Rotation X °", "X rotation °", "Вращение X °", "X 旋转 °"], ["Rotation Y °", "Y rotation °", "Вращение Y °", "Y 旋转 °"], ["Rotation Z °", "Z rotation °", "Вращение Z °", "Z 旋转 °"],
    ["Équiper dans l'aperçu", "Equip in preview", "Оснастить в предпросмотре", "在预览中装备"],
    ["Retirer l'emplacement", "Remove slot", "Очистить ячейку", "移除装备槽"],
    ["La sélection reste locale et ne modifie aucun fichier.", "The selection stays local and changes no files.", "Выбор остаётся локальным и не изменяет файлы.", "选择仅保存在本地，不会修改文件。"],
    ["Équipements affichés", "Displayed equipment", "Отображаемое снаряжение", "显示的装备"],
    ["Aucun équipement affiché.", "No equipment displayed.", "Снаряжение не отображается.", "未显示装备。"],
    ["Restauration en attente.", "Waiting to restore.", "Ожидает восстановления.", "等待恢复。"],
    ["Squelette chargé · choisir un clip pour tester.", "Skeleton loaded · choose a clip to test.", "Скелет загружен · выберите клип для проверки.", "骨架已加载 · 选择片段进行测试。"],
    ["Statique dans l'espace commun.", "Static in shared space.", "Статично в общем пространстве.", "在共享空间中保持静态。"],
    ["Équipement affiché dans l'aperçu. Vérifie sa position et son mouvement.", "Equipment shown in preview. Check its position and movement.", "Снаряжение показано в предпросмотре. Проверьте положение и движение.", "装备已显示在预览中，请检查位置和动作。"],
    ["Équipement retiré de l'aperçu.", "Equipment removed from preview.", "Снаряжение удалено из предпросмотра.", "装备已从预览中移除。"],
    ["Modèle absent du projet ; recherche une autre ressource.", "Model missing from project; search for another resource.", "Модель отсутствует в проекте; найдите другой ресурс.", "项目中缺少模型；请搜索其他资源。"],
    ["Le modèle n'est plus présent dans le projet.", "The model is no longer in the project.", "Модель больше не находится в проекте.", "模型已不在项目中。"],
    ["Choisis d'abord le personnage et un autre modèle.", "First choose a character and another model.", "Сначала выберите персонажа и другую модель.", "请先选择角色和另一个模型。"],
    ["Choisis d'abord un modèle.", "First choose a model.", "Сначала выберите модель.", "请先选择模型。"],
    ["Chargement de l'équipement…", "Loading equipment…", "Загрузка снаряжения…", "正在加载装备…"],
    ["Aucun clip avec en-tête pris en charge", "No clip with a supported header", "Нет клипов с поддерживаемым заголовком", "没有标头受支持的片段"],
    ["Aucun en-tête de clip squelettique pris en charge.", "No skeletal clip with a supported header.", "Нет скелетных клипов с поддерживаемым заголовком.", "没有标头受支持的骨骼片段。"],
    ["L'ancien équipement est conservé.", "The previous equipment is kept.", "Предыдущее снаряжение сохранено.", "保留之前的装备。"],
    ["Aucun équipement n'a été ajouté.", "No equipment was added.", "Снаряжение не добавлено.", "未添加装备。"],
    ["Vue 3D indisponible", "3D view unavailable", "3D недоступно", "3D 视图不可用"],
    ["Squelette du modèle indisponible", "Model skeleton unavailable", "Скелет модели недоступен", "模型骨架不可用"],
    ["Tenue statique", "Static outfit", "Статичная одежда", "静态服装"],
    ["Tenue non affichée", "Outfit not displayed", "Одежда не отображается", "服装未显示"],
    ["Équipement non affiché", "Equipment not displayed", "Снаряжение не отображается", "装备未显示"],
    ["Non restauré", "Not restored", "Не восстановлено", "未恢复"],
    ["FSR 1 indisponible", "FSR 1 unavailable", "FSR 1 недоступен", "FSR 1 不可用"],
    ["Animation interrompue, pose de repos", "Animation stopped, rest pose restored", "Анимация остановлена, восстановлена исходная поза", "动画已停止，已恢复静止姿势"],
    ["Contexte WebGL perdu.", "WebGL context lost.", "Контекст WebGL потерян.", "WebGL 上下文已丢失。"],
    ["Transformation d'équipement invalide.", "Invalid equipment transform.", "Некорректное преобразование снаряжения.", "装备变换无效。"],
    ["Os d'attache à échelle nulle.", "Attachment bone has zero scale.", "Масштаб кости крепления равен нулю.", "附着骨骼的缩放为零。"],
    ["Matrice d'animation singulière.", "Singular animation matrix.", "Вырожденная матрица анимации.", "动画矩阵不可逆。"],
    ["Matrice d'animation non finie.", "Non-finite animation matrix.", "Неконечная матрица анимации.", "动画矩阵包含非有限值。"],
    ["Matrice d'animation trop grande pour WebGL.", "Animation matrix is too large for WebGL.", "Матрица анимации слишком велика для WebGL.", "动画矩阵超出 WebGL 的范围。"],
    ["Transformation non finie.", "Non-finite transform.", "Неконечное преобразование.", "变换包含非有限值。"],
    ["Quaternion d'animation invalide.", "Invalid animation quaternion.", "Некорректный кватернион анимации.", "动画四元数无效。"],
    ["Quaternion d'os invalide.", "Invalid bone quaternion.", "Некорректный кватернион кости.", "骨骼四元数无效。"],
    ["Mode d'échelle Softimage FSKA non pris en charge.", "FSKA Softimage scale mode is not supported.", "Режим масштаба Softimage в FSKA не поддерживается.", "不支持 FSKA 的 Softimage 缩放模式。"],
    ["Choisis un os reconnu du modèle de base.", "Choose a recognized bone from the base model.", "Выберите распознанную кость базовой модели.", "请选择基础模型中已识别的骨骼。"],
    ["Composition trop volumineuse : aucune géométrie n'a été supprimée.", "Composition too large: no geometry was removed.", "Композиция слишком велика: геометрия не удалена.", "组合过大：未删除任何几何体。"],
    ["Skins de la composition supérieurs à 64 Mio.", "Composition skins exceed 64 MiB.", "Скины композиции превышают 64 МиБ.", "组合的皮肤超过 64 MiB。"],
    ["Aucun os animé de ce clip ne correspond au modèle.", "No animated bone in this clip matches the model.", "Анимированные кости клипа не соответствуют модели.", "此片段中没有与模型匹配的动画骨骼。"],
    ["Skin et géométrie ne correspondent pas.", "Skin and geometry do not match.", "Скин и геометрия не совпадают.", "皮肤与几何体不匹配。"],
    ["Squelette de skin invalide.", "Invalid skin skeleton.", "Некорректный скелет скина.", "皮肤骨架无效。"],
    ["albédos limités à 1 024 px dans la vue", "albedos limited to 1,024 px in the view", "альбедо ограничено 1 024 пикселями", "视图中的反照率纹理限制为 1,024 像素"],
    ["texture indisponible", "texture unavailable", "текстура недоступна", "纹理不可用"],
    ["mémoire texture insuffisante", "insufficient texture memory", "недостаточно памяти для текстур", "纹理内存不足"],
    ["FSR sans anticrénelage multisample", "FSR without multisample anti-aliasing", "FSR без мультисэмплинга", "FSR 未启用多重采样抗锯齿"],
    ["Un aperçu, pas une validation en jeu.", "A preview, not an in-game validation.", "Предпросмотр не является проверкой в игре.", "这只是预览，不代表游戏内验证。"],
    ["Les catégories et associations sont indicatives. Os, proportions, morphs, matières, collisions et comportements peuvent différer. Les modèles requis ne sont pas toujours déductibles d'un clip : ajoute-les manuellement.", "Categories and associations are indicative. Bones, proportions, morphs, materials, collisions and behavior may differ. Required models cannot always be inferred from a clip: add them manually.", "Категории и связи приблизительны. Кости, пропорции, морфы, материалы, столкновения и поведение могут отличаться. Необходимые модели не всегда можно определить по клипу: добавляйте их вручную.", "类别和关联仅供参考。骨骼、比例、变形、材质、碰撞和行为可能不同。无法始终从片段推断所需模型：请手动添加。"],
    ["Composer dans Player", "Compose in Player", "Собрать в Player", "在 Player 中组合"],
    ["Aller à Player", "Go to Player", "Перейти в Player", "前往 Player"],
    ["Retrouver des modèles pour une animation", "Find models for an animation", "Найти модели для анимации", "为动画查找模型"],
    ["Suggestions d'après les noms uniquement : les tenues et skins sans nom reconnaissable peuvent manquer. La présence d'un modèle ne prouve pas sa compatibilité avec le clip.", "Suggestions use names only: outfits and skins without recognizable names may be missing. A model's presence does not prove clip compatibility.", "Предложения основаны только на именах: одежда и скины с нераспознаваемыми именами могут отсутствовать. Наличие модели не подтверждает совместимость с клипом.", "仅根据名称推荐：名称无法识别的服装和皮肤可能遗漏。列出模型不代表它与片段兼容。"],
    ["Rechercher une animation", "Search animations", "Поиск анимации", "搜索动画"], ["Choisir une animation…", "Choose an animation…", "Выберите анимацию…", "选择动画…"],
    ["Choisis une animation pour chercher des modèles.", "Choose an animation to find models.", "Выберите анимацию для поиска моделей.", "选择动画以查找模型。"],
    ["Aucune animation trouvée ; vérifie le filtre ou le scan du projet.", "No animation found; check the filter or project scan.", "Анимации не найдены; проверьте фильтр или сканирование проекта.", "未找到动画；请检查筛选条件或项目扫描。"],
    ["Aucun modèle suggéré par nom. Le choix manuel reste possible dans Animations et Player.", "No model suggested by name. You can still choose manually in Animations and Player.", "По имени не предложено моделей. Вы можете выбрать их вручную в разделах «Анимации» и Player.", "没有按名称推荐的模型；仍可在动画和 Player 中手动选择。"],
    ["Pour assembler une tenue, ouvre d'abord le personnage comme base dans Player, puis sélectionne la tenue et son emplacement. Pour un modèle absent des suggestions, ouvre l'animation dans la vue Animations et choisis-le manuellement ; ou sélectionne « Tous les modèles » dans Player.", "To assemble an outfit, first open the character as the base in Player, then choose the outfit and its slot. For a missing model, open the animation in Animations and choose it manually, or select All models in Player.", "Чтобы собрать комплект, сначала откройте персонажа как базовую модель в Player, затем выберите одежду и ячейку. Если модели нет в предложениях, откройте анимацию в разделе «Анимации» и выберите её вручную либо выберите все модели в Player.", "要组合服装，请先在 Player 中将角色作为基础模型打开，然后选择服装及其装备槽。对于未推荐的模型，请在动画视图中打开动画并手动选择，或在 Player 中选择所有模型。"],
    ["Voir la fiche", "View details", "Открыть карточку", "查看详情"], ["Ouvrir comme base dans Player", "Open as base in Player", "Открыть как базовую модель в Player", "在 Player 中作为基础模型打开"],
    ["Nom exact", "Exact name", "Точное имя", "名称完全匹配"], ["Nom proche", "Similar name", "Похожее имя", "名称相近"],
    ["Même famille de nom", "Same name family", "Одна группа имён", "同一名称类别"],
    ["Âge indiqué dans le nom", "Age indicated by name", "Возраст указан в имени", "名称中标明年龄"],
    ["personnage", "character", "персонаж", "角色"], ["tenue / skin", "outfit / skin", "одежда / скин", "服装 / 皮肤"],
    ["objet", "item", "предмет", "物品"], ["non vérifié", "unverified", "не проверено", "未验证"],
    ["Vue 3D & animations", "3D view & animations", "3D и анимация", "3D 视图与动画"],
    ["Rendu interactif", "Interactive rendering", "Интерактивный рендеринг", "交互式渲染"],
    ["FSR 1 expérimental (WebGL2)", "Experimental FSR 1 (WebGL2)", "Экспериментальный FSR 1 (WebGL2)", "实验性 FSR 1（WebGL2）"],
    ["Niveau d'upscaling FSR 1", "FSR 1 upscaling level", "Уровень масштабирования FSR 1", "FSR 1 升频等级"],
    ["Ultra qualité · 77 %", "Ultra quality · 77%", "Ультракачество · 77%", "超高质量 · 77%"],
    ["Qualité · 67 %", "Quality · 67%", "Качество · 67%", "质量 · 67%"], ["Équilibré · 59 %", "Balanced · 59%", "Баланс · 59%", "平衡 · 59%"],
    ["Performance · 50 %", "Performance · 50%", "Производительность · 50%", "性能 · 50%"],
    ["Netteté FSR 1", "FSR 1 sharpness", "Резкость FSR 1", "FSR 1 锐化"], ["Anticrénelage FSR 1", "FSR 1 anti-aliasing", "Сглаживание FSR 1", "FSR 1 抗锯齿"],
    ["Désactivé · économie GPU", "Off · save GPU", "Выключено · экономия GPU", "关闭 · 节省 GPU"],
    ["Densité maximale de l'aperçu", "Maximum preview density", "Максимальная плотность предпросмотра", "预览最大像素密度"],
    ["×1 · économie GPU", "×1 · save GPU", "×1 · экономия GPU", "×1 · 节省 GPU"], ["×2 · écrans haute densité", "×2 · high-density displays", "×2 · экраны высокой плотности", "×2 · 高密度屏幕"],
    ["Taille maximale du canvas", "Maximum canvas size", "Максимальный размер холста", "画布最大尺寸"],
    ["800 px · économie GPU", "800 px · save GPU", "800 px · экономия GPU", "800 像素 · 节省 GPU"],
    ["Images par seconde en animation", "Animation frames per second", "Кадров в секунду анимации", "动画帧率"],
    ["15 i/s · économie GPU", "15 fps · save GPU", "15 кадров/с · экономия GPU", "15 帧/秒 · 节省 GPU"], ["30 i/s", "30 fps", "30 кадров/с", "30 帧/秒"], ["60 i/s", "60 fps", "60 кадров/с", "60 帧/秒"],
    ["FSR 1 est spatial, pas FSR 4. Il réduit la résolution de la scène, pas les triangles ; les deux passes peuvent être plus lentes sur certains GPU. Les exports et PNG ne sont pas modifiés.", "FSR 1 is spatial, not FSR 4. It reduces scene resolution, not triangles; the two passes can be slower on some GPUs. Exports and PNGs are unchanged.", "FSR 1 — пространственное масштабирование, не FSR 4. Оно снижает разрешение сцены, а не число треугольников; два прохода на некоторых GPU могут быть медленнее. Экспорт и PNG не изменяются.", "FSR 1 是空间升频，而非 FSR 4。它降低场景分辨率，不减少三角形；两次处理在某些 GPU 上可能更慢。导出和 PNG 不受影响。"],
    // Asset-focused home and safe session recovery.
    ["Explorer les ressources", "Explore assets", "Изучайте ресурсы", "浏览资源"],
    ["Modèles, textures et animations", "Models, textures and animations", "Модели, текстуры и анимация", "模型、纹理和动画"],
    ["02 / MODÈLES", "02 / MODELS", "02 / МОДЕЛИ", "02 / 模型"],
    ["Explorer les modèles", "Explore models", "Изучить модели", "浏览模型"],
    ["Voir les modèles", "View models", "Посмотреть модели", "查看模型"],
    ["03 / PLAYER", "03 / PLAYER", "03 / PLAYER", "03 / PLAYER"],
    ["Ouvrir le vestiaire", "Open the wardrobe", "Открыть гардероб", "打开衣柜"],
    ["Ouvrir Player", "Open Player", "Открыть Player", "打开 Player"],
    ["ATELIER / RESSOURCES", "WORKSHOP / ASSETS", "МАСТЕРСКАЯ / РЕСУРСЫ", "工作区 / 资源"],
    ["Les modifications sont enregistrées dans le dossier de travail.", "Changes are saved in the work folder.", "Изменения сохраняются в рабочей папке.", "更改保存在工作文件夹中。"],
    ["La source du projet reste intacte ; la copie modifiée est dans le dossier de travail.", "The project source stays intact; the modified copy is in the work folder.", "Исходные файлы проекта остаются нетронутыми; изменённая копия находится в рабочей папке.", "项目源文件保持不变；修改后的副本位于工作文件夹中。"],
    ["Player : personnages, tenues et objets (Ctrl+4)", "Player: characters, outfits and items (Ctrl+4)", "Player: персонажи, одежда и предметы (Ctrl+4)", "Player：角色、服装和物品 (Ctrl+4)"],
    ["La session ou les réglages n'ont pas pu être enregistrés. Quitter quand même ?", "The session or settings could not be saved. Quit anyway?", "Не удалось сохранить сеанс или настройки. Всё равно выйти?", "无法保存会话或设置。仍要退出吗？"],
    ["Vérifier", "Check", "Проверить", "检查"],
    ["Objet", "Item", "Предмет", "物品"], ["Retirer", "Remove", "Удалить", "移除"],
    ["Aucun", "None", "Нет", "无"], ["élément(s)", "element(s)", "элемент(ов)", "个元素"],
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
    ["Son", "Sound", "Звук", "声音"],

     ["Outils", "Tools", "Инструменты", "工具"],
    ["Réglages", "Settings", "Настройки", "设置"], ["Journal", "Log", "Журнал", "日志"],
    ["Chargement du projet…", "Loading project…", "Загрузка проекта…", "正在加载项目…"],

    ["01 / ATELIER", "01 / WORKSHOP", "01 / МАСТЕРСКАЯ", "01 / 工作区"],
    ["Modifier l'existant", "Edit existing assets", "Изменить существующие ресурсы", "修改现有资源"],
    ["Explorer les modèles, retoucher les textures et travailler avec Blender.", "Explore models, edit textures and work with Blender.", "Изучайте модели, редактируйте текстуры и работайте в Blender.", "浏览模型、编辑纹理并使用 Blender。"],
    ["Ouvrir la modification", "Open editing", "Открыть редактор", "打开修改界面"],

    ["Projet", "Project", "Проект", "项目"], ["Export complet", "Full export", "Полный экспорт", "完整导出"],
    ["BFRES auxiliaires (rapport)", "Auxiliary BFRES (report)", "Вспомогательные BFRES (отчёт)", "辅助 BFRES（报告）"],
    ["Exporte les textures reconnues en PNG et les modèles en glTF et OBJ, y compris dans les packs. Les BFRES auxiliaires ne sont pas encore exportés.", "Export recognized textures as PNG and models as glTF and OBJ, including inside packs. Auxiliary BFRES files are not yet exported.", "Экспорт распознанных текстур в PNG и моделей в glTF и OBJ, включая файлы в пакетах. Вспомогательные BFRES пока не экспортируются.", "将识别出的纹理导出为 PNG，模型导出为 glTF 和 OBJ，包括包内资源。辅助 BFRES 文件尚不支持导出。"],
    ["Lancer l'export complet", "Start full export", "Начать полный экспорт", "开始完整导出"],
    ["Dernières actions", "Recent actions", "Последние действия", "最近操作"],

    ["Explore les ressources du projet et travaille sur des copies, sans écraser les fichiers source.", "Explore project assets and work on copies without overwriting source files.", "Изучайте ресурсы проекта и работайте с копиями, не перезаписывая исходные файлы.", "浏览项目资源并处理副本，不覆盖源文件。"],

    ["Que veux-tu modifier ou explorer ?", "What would you like to edit or explore?", "Что вы хотите изменить или изучить?", "要修改或浏览什么？"],
    ["Inspecter, prévisualiser et exporter la géométrie reconnue.", "Inspect, preview and export recognized geometry.", "Изучайте, просматривайте и экспортируйте распознанную геометрию.", "检查、预览并导出识别出的几何体。"],
    ["Retrouver les textures et animations associées aux modèles.", "Find textures and animations associated with models.", "Найдите текстуры и анимации, связанные с моделями.", "查找与模型关联的纹理和动画。"],
    ["Exporter ou remplacer une image dans les formats pris en charge.", "Export or replace an image in supported formats.", "Экспортируйте или замените изображение в поддерживаемых форматах.", "以支持的格式导出或替换图像。"],
    ["Sons (expérimental)", "Sounds (experimental)", "Звуки (экспериментально)", "声音（实验性）"],
    ["Écouter, exporter et remplacer les sons BWAV compatibles.", "Play, export and replace compatible BWAV sounds.", "Прослушивайте, экспортируйте и заменяйте совместимые звуки BWAV.", "播放、导出和替换兼容的 BWAV 声音。"],
    ["Importer un modèle", "Import a model", "Импорт модели", "导入模型"],
    ["Analyser un OBJ ou glTF et injecter sa géométrie dans une cible compatible.", "Analyze an OBJ or glTF and inject its geometry into a compatible target.", "Проанализируйте OBJ или glTF и внедрите геометрию в совместимую цель.", "分析 OBJ 或 glTF，并将其几何体注入兼容的目标。"],
    ["Les originaux restent intacts.", "The originals remain intact.", "Оригиналы остаются нетронутыми.", "原始文件保持不变。"],
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
    ["Télécharger le GLB", "Download GLB", "Скачать GLB", "下载 GLB"],
    ["Export en cours…", "Exporting…", "Экспорт…", "正在导出…"],
    ["GLB prêt · export partiel", "GLB ready · partial export", "GLB готов · частичный экспорт", "GLB 已就绪 · 部分导出"],
    ["Export animé refusé", "Animated export refused", "Экспорт анимации отклонён", "动画导出被拒绝"],
    ["Aucun nouveau GLB n'a été téléchargé.", "No new GLB was downloaded.", "Новый GLB не был загружен.", "未下载新的 GLB。"],
    ["Un seul GLB est téléchargé. Le détail des clips et des omissions se trouve aussi dans RAPPORT_ANIMATIONS.txt du dossier.", "One GLB is downloaded. Clip details and omissions are also in RAPPORT_ANIMATIONS.txt in the folder.", "Загружается один GLB. Сведения о клипах и пропусках также находятся в RAPPORT_ANIMATIONS.txt в папке.", "将下载一个 GLB。片段详情和遗漏内容也记录在该文件夹的 RAPPORT_ANIMATIONS.txt 中。"],
    ["Si le téléchargement ne démarre pas, utilise le bouton ou ouvre ce dossier.", "If the download does not start, use the button or open this folder.", "Если загрузка не началась, воспользуйтесь кнопкой или откройте эту папку.", "如果下载没有开始，请使用按钮或打开此文件夹。"],
    ["Quitter l'atelier (arrête le serveur)", "Quit workshop (stops the server)", "Выйти из мастерской (остановить сервер)", "退出工作区（停止服务器）"],
    ["Réduire l'interface (mode compact, sans rail)", "Reduce interface (compact mode, no sidebar)", "Свернуть интерфейс (компактный режим без панели)", "缩小界面（紧凑模式，隐藏侧栏）"],
    ["Plein écran", "Fullscreen", "Полный экран", "全屏"], ["Palette de commandes (Ctrl+K)", "Command palette (Ctrl+K)", "Палитра команд (Ctrl+K)", "命令面板 (Ctrl+K)"],
    ["Projet ouvert", "Open project", "Открытый проект", "已打开项目"], ["Accueil (Ctrl+1)", "Home (Ctrl+1)", "Главная (Ctrl+1)", "主页 (Ctrl+1)"],
    ["Espace modification", "Editing workspace", "Область редактирования", "修改工作区"], ["Modèles (Ctrl+2)", "Models (Ctrl+2)", "Модели (Ctrl+2)", "模型 (Ctrl+2)"],
    ["Ensembles : modèle + textures + animations", "Collections: model + textures + animations", "Наборы: модель + текстуры + анимации", "资源集：模型 + 纹理 + 动画"],
    ["Animations squelettiques (expérimental)", "Skeletal animations (experimental)", "Скелетная анимация (экспериментально)", "骨骼动画（实验性）"],
    ["Textures (Ctrl+3)", "Textures (Ctrl+3)", "Текстуры (Ctrl+3)", "纹理 (Ctrl+3)"], ["Sons BWAV (expérimental)", "BWAV sounds (experimental)", "Звуки BWAV (экспериментально)", "BWAV 声音（实验性）"],
    ["Importer un modèle (Ctrl+5)", "Import a model (Ctrl+5)", "Импорт модели (Ctrl+5)", "导入模型 (Ctrl+5)"],
     ["Réglages (Ctrl+6)", "Settings (Ctrl+6)", "Настройки (Ctrl+6)", "设置 (Ctrl+6)"], ["Journal (Ctrl+J)", "Log (Ctrl+J)", "Журнал (Ctrl+J)", "日志 (Ctrl+J)"],
    ["Ces fichiers seront signalés comme ignorés dans le rapport", "These files will be marked as skipped in the report", "Эти файлы будут отмечены в отчёте как пропущенные", "这些文件将在报告中标记为已跳过"],
    ["Filtrer… (Ctrl+F)", "Filter… (Ctrl+F)", "Фильтр… (Ctrl+F)", "筛选… (Ctrl+F)"], ["Filtrer…", "Filter…", "Фильтр…", "筛选…"],
    ["Ne montrer qu'un type de carte", "Show only one map type", "Показать только один тип карты", "仅显示一种贴图类型"],
     ["Nom ou chemin", "Name or path", "Имя или путь", "名称或路径"],
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
    ["Télécharger 1 GLB · toutes les animations détectées", "Download 1 GLB · all detected animations", "Скачать 1 GLB · все найденные анимации", "下载 1 个 GLB · 所有检测到的动画"],
    ["Le GLB groupé inclut les clips des fichiers suggérés par nom et le fichier d'aperçu sélectionné. L'association est indicative : d'autres fichiers peuvent manquer ; les clips incompatibles sont signalés.", "The combined GLB includes clips from files suggested by name and the selected preview file. Matching is heuristic: other files may be missing; incompatible clips are reported.", "Общий GLB включает клипы из файлов, предложенных по имени, и выбранного файла предпросмотра. Сопоставление приблизительное: другие файлы могут отсутствовать; несовместимые клипы указываются.", "合并的 GLB 包含按名称推荐的文件及所选预览文件中的片段。匹配仅供参考：其他文件可能遗漏；不兼容的片段会被报告。"],
    ["Aucune animation suggérée : ouvre un fichier depuis Animations et choisis ce modèle.", "No suggested animation: open a file from Animations and choose this model.", "Нет предложенных анимаций: откройте файл в разделе «Анимации» и выберите эту модель.", "没有推荐的动画：请从“动画”中打开文件并选择此模型。"],
    ["Association par nom proposée à titre indicatif. Ouvre un modèle : le bouton de GLB groupé inclura tous les clips de ce fichier et ceux suggérés pour le modèle. OBJ/MTL restent statiques et les fichiers du jeu ne sont pas modifiés.", "Name matching is indicative. Open a model: the combined GLB button includes all clips from this file and those suggested for the model. OBJ/MTL remain static; game files are not modified.", "Сопоставление по имени приблизительное. Откройте модель: кнопка общего GLB добавит все клипы этого файла и предложенных для модели файлов. OBJ/MTL статичны; файлы игры не изменяются.", "按名称匹配仅供参考。打开模型后，合并 GLB 按钮将包含此文件及推荐文件中的所有片段。OBJ/MTL 保持静态，游戏文件不会被修改。"],
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
     ["aucun", "none", "нет", "无"],
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

    ["Quitter l'atelier ? Le serveur local va s'arrêter.", "Quit the workshop? The local server will stop.", "Выйти из мастерской? Локальный сервер остановится.", "退出工作区？本地服务器将停止。"],

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
    m = normalize(source).match(/^(\d+) clip\(s\) squelettique\(s\), (\d+) avec en-tête pris en charge ; courbes vérifiées à l'ouverture\.$/);
    if (m) return { en: `${m[1]} skeletal clip(s), ${m[2]} with a supported header; curves are checked on opening.`, ru: `Скелетных клипов: ${m[1]}, с поддерживаемым заголовком: ${m[2]}; кривые проверяются при открытии.`, zh: `${m[1]} 个骨骼片段，其中 ${m[2]} 个标头受支持；打开时检查曲线。` }[language];
    m = source.trim().match(/^([\s\S]+) · (\d+) images$/);
    if (m) return { en: `${m[1]} · ${m[2]} frames`, ru: `${m[1]} · кадров: ${m[2]}`, zh: `${m[1]} · ${m[2]} 帧` }[language];
    m = normalize(source).match(/^(\d+) os associés(?:, (\d+) absents)? · 30 images\/s par convention · lecture expérimentale, non validée en jeu\.$/);
    if (m) return { en: `${m[1]} bones matched${m[2] ? `, ${m[2]} missing` : ""} · 30 fps by convention · experimental playback, not validated in game.`, ru: `Связано костей: ${m[1]}${m[2] ? `, отсутствует: ${m[2]}` : ""} · 30 кадров/с по соглашению · экспериментальный предпросмотр, не проверен в игре.`, zh: `已关联 ${m[1]} 根骨骼${m[2] ? `，缺少 ${m[2]} 根` : ""} · 按惯例使用 30 帧/秒 · 实验性播放，未经游戏内验证。` }[language];
    m = normalize(source).match(/^(\d+) candidat\(s\) par nom · (\d+) affichés\. Les catégories restent indicatives\.$/);
    if (m) return { en: `${m[1]} name-based candidate(s) · ${m[2]} shown. Categories are indicative.`, ru: `Кандидатов по имени: ${m[1]} · показано ${m[2]}. Категории приблизительны.`, zh: `按名称找到 ${m[1]} 个候选项 · 显示 ${m[2]} 个。类别仅供参考。` }[language];
    m = normalize(source).match(/^(\d+) animation\(s\) trouvée\(s\) · (\d+) affichées\. Choisis-en une\.$/);
    if (m) return { en: `${m[1]} animation(s) found · ${m[2]} shown. Choose one.`, ru: `Найдено анимаций: ${m[1]} · показано ${m[2]}. Выберите одну.`, zh: `找到 ${m[1]} 个动画 · 显示 ${m[2]} 个。请选择一个。` }[language];
    m = normalize(source).match(/^(\d+) modèle\(s\) suggéré\(s\) par nom · (\d+) affichés\. À vérifier dans Player\.$/);
    if (m) return { en: `${m[1]} model(s) suggested by name · ${m[2]} shown. Check in Player.`, ru: `Предложено моделей по имени: ${m[1]} · показано ${m[2]}. Проверьте в Player.`, zh: `按名称推荐 ${m[1]} 个模型 · 显示 ${m[2]} 个。请在 Player 中检查。` }[language];
    m = normalize(source).match(/^(\d+) os associés(?: · (\d+) pistes absentes)? · 30 images\/s par convention\. Vérifie aussi les tenues\.$/);
    if (m) return { en: `${m[1]} bones matched${m[2] ? ` · ${m[2]} missing tracks` : ""} · 30 fps by convention. Check outfits too.`, ru: `Связано костей: ${m[1]}${m[2] ? ` · отсутствует дорожек: ${m[2]}` : ""} · 30 кадров/с по соглашению. Проверьте и одежду.`, zh: `已关联 ${m[1]} 根骨骼${m[2] ? ` · 缺少 ${m[2]} 条轨道` : ""} · 按惯例使用 30 帧/秒。也请检查服装。` }[language];
    m = normalize(source).match(/^(\d+) os associés · aperçu expérimental$/);
    if (m) return { en: `${m[1]} bones matched · experimental preview`, ru: `Связано костей: ${m[1]} · экспериментальный предпросмотр`, zh: `已关联 ${m[1]} 根骨骼 · 实验性预览` }[language];
    m = source.trim().match(/^Attaché à ([\s\S]+) · position à vérifier\.$/);
    if (m) return { en: `Attached to ${m[1]} · check position.`, ru: `Прикреплено к ${m[1]} · проверьте положение.`, zh: `附着到 ${m[1]} · 请检查位置。` }[language];
    m = normalize(source).match(/^(\d+) résultat\(s\) · (\d+) affichés$/);
    if (m) return { en: `${m[1]} result(s) · ${m[2]} shown`, ru: `Результатов: ${m[1]} · показано ${m[2]}`, zh: `${m[1]} 个结果 · 显示 ${m[2]} 个` }[language];
    m = normalize(source).match(/^(\d+) piste\(s\) sans os correspondant$/);
    if (m) return { en: `${m[1]} track(s) without a matching bone`, ru: `Дорожек без соответствующей кости: ${m[1]}`, zh: `${m[1]} 条轨道没有对应的骨骼` }[language];
    m = source.trim().match(/^Équipement non ajouté : ([\s\S]+) (L'ancien équipement est conservé\.|Aucun équipement n'a été ajouté\.)$/);
    if (m) return { en: `Equipment not added: ${lookup(m[1])} ${lookup(m[2])}`, ru: `Снаряжение не добавлено: ${lookup(m[1])} ${lookup(m[2])}`, zh: `未添加装备：${lookup(m[1])} ${lookup(m[2])}` }[language];
    m = source.trim().match(/^(Tête|Tenue|Jambes|Main droite|Main gauche) non restauré : ([\s\S]+)$/);
    if (m) return { en: `${lookup(m[1])} not restored: ${lookup(m[2])}`, ru: `${lookup(m[1])} не восстановлено: ${lookup(m[2])}`, zh: `${lookup(m[1])}未恢复：${lookup(m[2])}` }[language];
    m = source.trim().match(/^Compensation d'échelle impossible pour « ([\s\S]+) » : échelle parent nulle\.$/);
    if (m) return { en: `Scale compensation is impossible for “${m[1]}”: parent scale is zero.`, ru: `Компенсация масштаба для «${m[1]}» невозможна: масштаб родителя равен нулю.`, zh: `无法为“${m[1]}”补偿缩放：父级缩放为零。` }[language];
    m = source.trim().match(/^Rotation Euler partielle sans base pour l'os quaternion « ([\s\S]+) »\.$/);
    if (m) return { en: `Partial Euler rotation without a base for quaternion bone “${m[1]}”.`, ru: `Неполное вращение Эйлера без базового значения для кости-кватерниона «${m[1]}».`, zh: `四元数骨骼“${m[1]}”的欧拉旋转不完整且缺少基值。` }[language];
    m = source.trim().match(/^(Animation indisponible|Animation interrompue, pose de repos|Vue 3D indisponible|Squelette du modèle indisponible|Tenue statique|Tenue non affichée|Équipement non affiché|Non restauré|FSR 1 indisponible) : ([\s\S]+)$/);
    if (m) return `${lookup(m[1])} : ${lookup(m[2])}`;
    m = normalize(source).match(/^image plafonnée à (\d+) px \/ densité ×(\d+)$/);
    if (m) return { en: `image capped at ${m[1]} px / density ×${m[2]}`, ru: `изображение ограничено ${m[1]} пикс. / плотность ×${m[2]}`, zh: `图像限制为 ${m[1]} 像素 / 密度 ×${m[2]}` }[language];
    m = normalize(source).match(/^plus de (\d+) textures : certaines non affichées$/);
    if (m) return { en: `more than ${m[1]} textures: some not displayed`, ru: `текстур больше ${m[1]}: некоторые не отображаются`, zh: `超过 ${m[1]} 个纹理：部分未显示` }[language];
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
    m = normalize(source).match(/^(\d+) dans le dossier de travail$/);
    if (m) return { en: `${m[1]} in the work folder`, ru: `В рабочей папке: ${m[1]}`, zh: `工作文件夹中有 ${m[1]} 项` }[language];
    m = source.trim().match(/^Ouvrir (le modèle|la texture) ([\s\S]+)$/);
    if (m) return { en: `Open ${m[1] === "le modèle" ? "model" : "texture"} ${m[2]}`, ru: `Открыть ${m[1] === "le modèle" ? "модель" : "текстуру"} ${m[2]}`, zh: `打开${m[1] === "le modèle" ? "模型" : "纹理"} ${m[2]}` }[language];
    m = source.trim().match(/^Ouvrir ([\s\S]+)$/);
    if (m) return { en: `Open ${m[1]}`, ru: `Открыть ${m[1]}`, zh: `打开 ${m[1]}` }[language];
    m = normalize(source).match(/^(\d+) modèles repérés · (\d+) ressources auxiliaires · (\d+) textures repérées$/);
    if (m) return { en: `${m[1]} models found · ${m[2]} auxiliary assets · ${m[3]} textures found`, ru: `Моделей: ${m[1]} · вспомогательных ресурсов: ${m[2]} · текстур: ${m[3]}`, zh: `找到 ${m[1]} 个模型 · ${m[2]} 个辅助资源 · ${m[3]} 个纹理` }[language];
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
    m = normalize(source).match(/^(\d+) fichier\(s\) suggéré\(s\) par nom$/);
    if (m) return { en: `${m[1]} file(s) suggested by name`, ru: `Файлов, предложенных по имени: ${m[1]}`, zh: `按名称推荐 ${m[1]} 个文件` }[language];
    m = normalize(source).match(/^(\d+) fichier\(s\) d'animation suggéré\(s\)(?: · (\d+) affichés)?$/);
    if (m) return { en: `${m[1]} suggested animation file(s)${m[2] ? ` · ${m[2]} shown` : ""}`, ru: `Предложено файлов анимации: ${m[1]}${m[2] ? ` · показано ${m[2]}` : ""}`, zh: `推荐 ${m[1]} 个动画文件${m[2] ? ` · 显示 ${m[2]} 个` : ""}` }[language];
    m = normalize(source).match(/^GLB prêt · (\d+) clip\(s\)$/);
    if (m) return { en: `GLB ready · ${m[1]} clip(s)`, ru: `GLB готов · клипов: ${m[1]}`, zh: `GLB 已就绪 · ${m[1]} 个片段` }[language];
    m = normalize(source).match(/^GLB partiel : (\d+) clip\(s\) exporté\(s\)\. Consulte les omissions ci-dessous\.$/);
    if (m) return { en: `Partial GLB: ${m[1]} clip(s) exported. See omissions below.`, ru: `Частичный GLB: экспортировано клипов: ${m[1]}. Пропуски указаны ниже.`, zh: `部分 GLB：已导出 ${m[1]} 个片段。请查看下方遗漏内容。` }[language];
    m = normalize(source).match(/^Un GLB prêt : (\d+) clip\(s\), un cycle chacun\.$/);
    if (m) return { en: `One GLB ready: ${m[1]} clip(s), one cycle each.`, ru: `Один GLB готов: клипов ${m[1]}, по одному циклу.`, zh: `一个 GLB 已就绪：${m[1]} 个片段，每个一个周期。` }[language];
    m = source.trim().match(/^Texture référencée ([\s\S]+) : absente ou illisible, non reliée\.$/);
    if (m) return { en: `Referenced texture ${m[1]}: missing or unreadable, not linked.`, ru: `Текстура ${m[1]} отсутствует или нечитаема; не связана.`, zh: `引用的纹理 ${m[1]}：缺失或无法读取，未关联。` }[language];
    m = normalize(source).match(/^(\d+) forme\(s\) rigide\(s\) replacée\(s\) en pose de référence FSKL ; les triangles sont conservés\.$/);
    if (m) return { en: `${m[1]} rigid shape(s) placed in the FSKL rest pose; triangles are retained.`, ru: `Жёстких форм в исходной позе FSKL: ${m[1]}; треугольники сохранены.`, zh: `${m[1]} 个刚性形状已放置到 FSKL 参考姿势；保留所有三角形。` }[language];
    m = normalize(source).match(/^La source doit avoir (\d+) canal\(aux\) à (\d+) Hz \(24 Mio maximum\)\. Le remplacement produit un BWAV PCM16 dans la copie de travail, même si l'original est Opus ou DSP\. BARS, événements et RESTBL ne sont pas modifiés ; fonctionnement en jeu non garanti\.$/);
    if (m) return { en: `The source must have ${m[1]} channel(s) at ${m[2]} Hz (24 MiB maximum). Replacement writes a PCM16 BWAV working copy even if the original is Opus or DSP. BARS, events and RESTBL are not changed; in-game operation is not guaranteed.`, ru: `Источник должен иметь каналов: ${m[1]}, частота: ${m[2]} Гц (не более 24 МиБ). Замена создаёт рабочую копию PCM16 BWAV, даже если оригинал — Opus или DSP. BARS, события и RESTBL не изменяются; работа в игре не гарантируется.`, zh: `源文件必须具有 ${m[1]} 个声道、采样率 ${m[2]} Hz（最多 24 MiB）。即使原文件为 Opus 或 DSP，替换也会生成 PCM16 BWAV 工作副本。不会修改 BARS、事件或 RESTBL；不保证游戏内可用。` }[language];
    m = normalize(source).match(/^([\d\s, ]+) triangles · WebGL2 · résolution native(.*)$/);
    if (m) {
      const notes = m[2].split(" · ").map(lookup).join(" · ");
      return { en: `${m[1]} triangles · WebGL2 · native resolution${notes}`, ru: `Треугольников: ${m[1]} · WebGL2 · исходное разрешение${notes}`, zh: `${m[1]} 个三角形 · WebGL2 · 原生分辨率${notes}` }[language];
    }
    m = normalize(source).match(/^([\d\s, ]+) triangles · FSR 1 expérimental · (\d+) %(.*)$/);
    if (m) {
      const notes = m[3].split(" · ").map(lookup).join(" · ");
      return { en: `${m[1]} triangles · experimental FSR 1 · ${m[2]}%${notes}`, ru: `Треугольников: ${m[1]} · экспериментальный FSR 1 · ${m[2]}%${notes}`, zh: `${m[1]} 个三角形 · 实验性 FSR 1 · ${m[2]}%${notes}` }[language];
    }
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
