# 全参考模式改写输出格式指南

本指南说明全参考模式下改写输出的组织方式和编写规范。

六个改写部分全部使用英文编写。仅保留 `<d>` 内的对话和歌词，以及场景中可见文字的原始语言。

**描述细节：** 使 `detailed_description` 尽可能详尽明确。对于每个镜头，清楚建立当前的构图、主体外观与位置、环境与光照、动作与状态变化、镜头运动、当前声音，以及引用内容实际出现或生效的位置。避免将描述简化为剧情摘要或引用关系列表。

> 镜头、镜头运动、说话人、对话和普通声音的基本格式与视频提示编写指南（T2VA / I2VA / FL2VA / L2VA）共享。本指南聚焦于全参考模式特有的引用标签、分析部分和格式差异。

## 1. 整体结构

完整的改写输出由以下六个部分组成：

| 部分 | 用途 |
| --- | --- |
| `subject_definitions` | 定义引用内容及其引用标签 |
| `summary` | 总结任务类型、目标视频和主要引用关系 |
| `retention_analysis` | 描述引用内容如何被保留、迁移或复用 |
| `detailed_description` | 按播放顺序描述视觉、动作、镜头、声音和对话 |
| `overall_soundscape` | 总结环境氛围音和物理声音 |
| `non_diegetic_music` | 描述仅供观众听到的背景音乐 |

## 2. 引用标签与定义（`subject_definitions`）

全参考改写使用四种标签来标识引用内容的来源和角色：

| 标签 | 含义 |
| --- | --- |
| `<Subject N>` | 从参考素材中抽象出的可视内容，可在目标视频中复用或修改 |
| `<Picture N>` | 用作具体目标帧或镜头规划锚点的参考图片 |
| `<Video N>` | 提供剪辑源、续写起点或全视频时间结构的参考视频 |
| `<Audio N>` | 被复制或引用的音频信号 |

> 一旦为某内容分配了引用标签，它在 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description` 和音频部分中保持相同含义。

`subject_definitions` 定义每个后续需要单独追踪的引用内容，如人物、环境、源视频结构或音频轨道。每项单独一行，说明其标签所指代的内容、引用角色和需关注的主要特征；需要明确来源时命名相应的源素材。如果 `<Picture N>` 或 `<Video N>` 仅标识另一个引用项的来源，且后续不会被单独分析或使用，则在该项的定义中引用它，而不添加单独条目。`retention_analysis` 记录每个引用项出现的位置，以及它是被完整保留、部分保留、迁移还是复用。

### 2.1 `<Subject N>`

`<Subject N>` 用于可复用的可视内容，包括：

- 人物、动物或物体
- 场景、背景或环境
- 服装、道具、界面或视觉效果
- 风格、动作、表情或姿势

它代表将在目标视频中实际使用的内容单元，而非源文件本身。一个主体可由多个参考素材定义，一个参考素材也可提供多个主体。

```text
<Subject 1> 是 <Picture 1> 中的年轻女性，长黑发，蓝色开衫，银色细项链。
```

当同一主体来自多个素材时，合并来源并说明各素材提供的内容：

```text
<Subject 1> 是这位女性，外观来自 <Picture 1>，行走动作来自 <Video 1>。
```

### 2.2 `<Picture N>`

当参考图片本身用作镜头的首帧、关键帧、末帧、剪辑关键帧或构图锚点时，使用独立的 `<Picture N>`：

```text
<Picture 2> 是 [Shot 1] 的首帧，展示一位坐在咖啡馆窗边的女性。
```

如果图片仅用于定义角色、场景、服装或风格，不要创建独立的图片条目。而是在相应的 `<Subject N>` 定义中引用图片来源。

当图片用作分镜或镜头规划参考时，说明它映射到哪些镜头以及提供什么规划信息：

```text
<Picture 3> 是 [Shot 1] 和 [Shot 2] 的分镜参考，定义它们的视角、主体位置和镜头顺序。
```

### 2.3 `<Video N>`

`<Video N>` 保留用于全视频关系，如：

- 剪辑原始视频
- 从原始视频末尾续写
- 引用原始视频的镜头运动、剪切、节奏或时间结构

```text
<Video 1> 是目标视频剪辑的源视频。
```

如果参考视频中的人物、物体、场景、动作或效果作为可视内容被复用，它仍属于 `<Subject N>`。`<Video N>` 标识素材或结构来源，不替代主体标签。

### 2.4 `<Audio N>`

`<Audio N>` 代表独立的音频素材或参考视频中启用的同步音轨。常见用途包括：

- 复制全部或部分音频信号
- 引用背景音乐风格
- 引用说话人的音色和表达方式
- 使用原始音频中的对话、歌词或音效
- 引用节拍、节奏或音频连续性

当 `<Audio N>` 明确对应目标说话人时，在定义中复用该说话人的全局 ID：当说话人映射到已定义主体时写 `<Subject N> (Sx)`，否则使用稳定的声音描述后跟 `(Sx)`。ID 来自目标视频的全局说话人顺序，不在音频定义中独立分配或重新编号。说话人编号规则见第 5.4 节：

```text
<Audio 1> 是 <Subject 1> (S1) 的音色参考。
```

当一个音频素材承担多个角色时，用一个自然句子描述这些角色，而不是创建额外的子部分。

### 2.5 同一参考视频的视觉和音轨

`<Video N>` 和 `<Audio N>` 独立编号。每个索引仅表示标签在其类别中的顺序，不编码两类之间的配对关系。因此，同一参考视频可能对应 `<Video 1>` 和 `<Audio 2>`；不同索引不妨碍它们来自同一源素材。

普通参考视频不会仅仅因为文件包含声音而创建 `<Audio N>`。

`<Audio N>` 定义主要说明音频的角色，不必命名其来源的 `<Video N>`。仅在需要消除来源歧义时说明共同来源，例如：

```text
<Video 1> 是目标视频剪辑的源视频。
<Audio 2> 是 <Video 1> 的同步音轨，在目标视频中复用。
```

## 3. `summary`

本部分用一段简短的英文段落总结目标视频及其引用关系。以方括号任务类型前缀开头：

```text
[reference generation] ...
[video editing + reference generation + audio reuse] ...
```

根据各参考素材在目标视频中的实际角色选择任务类型：

| 任务类型 | 使用时机 |
| --- | --- |
| `keyframe completion` | 图片用作目标视频的首帧、关键帧、末帧、剪辑关键帧或其他具体帧锚点 |
| `reference generation` | 图片、视频或音频素材为角色、场景、风格、动作、镜头运动、分镜等提供生成引导，不作为具体帧或被剪辑/续写的源视频 |
| `video editing` | 直接修改现有源视频；剪辑图片或生成于静态关键帧之间不属于此类 |
| `video continuation` | 从现有源视频续写、延伸、恢复或过渡到新内容 |
| `audio reuse` | 同一音频信号被全部或部分复用 |
| `audio reference` | 音频信号不被直接复制；仅引用其音乐风格、音色、对话或歌词内容、音效质感、节拍或连续性 |

当任务满足多重关系时，用 ` + ` 组合任务类型，不重复同类。例如，从源视频续写同时使用图片作为末帧写为 `[video continuation + keyframe completion]`。剪辑源视频同时保留原始音频可写为 `[video editing + audio reuse]`。

视频或音频的存在不自动创建对应任务类型。如果参考视频仅提供镜头运动、剪切或节奏，通常属于 `reference generation`。仅当该视频被直接剪辑或续写时才使用 `video editing` 或 `video continuation`。

剪辑源视频时，如果原始音频仍然可听，也使用 `audio reuse`。续写源视频但不直接复制音频信号时，如果新音频仅延续原始音轨的可听特征，使用 `audio reference`。

摘要使用之前定义的 `<Subject N>`、`<Picture N>`、`<Video N>` 和 `<Audio N>` 标签来描述主要主体、镜头流和参考素材的角色。不要在本部分引入新的引用标签。

对于视频剪辑任务，在任务类型前缀后以以下内容开始摘要：

```text
The target video is an edited version of <Video 1>.
```

## 4. `retention_analysis`

本部分描述每个引用内容在目标视频中如何被保留、迁移、复制或引用。每个引用标签一行，保持 `subject_definitions` 中确立的含义。

### 4.1 可视内容

`<Subject N>`、`<Picture N>` 和 `<Video N>` 使用以下关系标记。这些标记是输出格式中的固定英文值：

| 关系标记 | 含义 |
| --- | --- |
| `fully_preserved` | 引用内容的定义角色被完整保留 |
| `partially_preserved` | 引用内容仍被使用，但部分定义特征被改变或仅部分保留 |
| `attribute_transfer` | 引用的特征被转移到不同的可识别目标主体 |
| `weak_reference` | 仅保留风格、类别、构图或氛围上的宽泛相似性 |

主体条目：

```text
<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - ...
```

图片条目：

```text
<Picture 2> ([Shot 1] first frame): fully_preserved - ...
```

视频结构条目：

```text
<Video 1> (cut and pacing structure): weak_reference - ...
```

### 4.2 音频

`<Audio N>` 使用以下关系标记：

| 关系标记 | 含义 |
| --- | --- |
| `fully_copy` | 完整源音频作为目标视频的完整最终音轨 |
| `partially_copy` | 仅复制部分时间线或选定音频层，或在复制后添加、移除或替换其他声音 |
| `reference` | 信号不被直接复制；仅引用音色、节奏、音乐风格、对话内容或声音质感 |
| `weak_reference` | 仅保留类别或氛围上的宽泛相似性 |

```text
<Audio 1>: fully_copy - <Audio 1> 1:1 复用作目标视频的完整最终音轨。
```

```text
<Audio 2>: reference - 目标说话人遵循 <Audio 2> 的音色和从容表达，不复制原始信号。
```

每个关系标记仅在 `subject_definitions` 中为该标签定义的引用角色范围内选择。不要将目标视频中新增加的动作、背景或剧情事件视为引用保真度的损失。

## 5. `detailed_description`

这是全参考改写的主体部分。它按目标视频播放顺序逐镜头描述视觉、动作、声音和对话，并在适用处插入引用标签。

### 5.1 基本格式

基本格式遵循视频提示编写指南（T2VA / I2VA / FL2VA / L2VA）：

- 主体用英文编写。保留对话、歌词和可见文字的原始语言。
- `[Shot 1]` 标记开头镜头，无时间戳。后续镜头使用 `[Shot N] At MM:SS.mmm, ...` 标记剪切时间。
- 镜头运动写为当前镜头内的自然英文，包括运动类型、幅度和速度（需要表达时）。
- 给声音源稳定的 `(S1)`、`(S2)` 等后续 ID。对话和歌词写为 `<d>[语言] ...</d>`。
- 对跨剪切的对话、被视频结尾截断的语音和跨镜头的连续音频，使用 `<scenetrans>`、`<cutoff>` 及相应的连续性描述。

关于镜头词汇、群体发言、画外音、跨剪切对话和可见文字的完整规则和示例，见视频提示编写指南（T2VA / I2VA / FL2VA / L2VA）。

### 5.2 全参考模式差异

| 维度 | T2VA | 全参考模式 |
| --- | --- | --- |
| 主要字段 | `integrated_multimodal_description` | `detailed_description` |
| 风格开头 | 写在 `[Shot 1]` 之后 | 在 `[Shot 1]` 之前用一两句英文确立 |
| 引用信息 | 不使用全参考标签 | 在首次出现及角色适用处插入 `<Subject N>`、`<Picture N>`、`<Video N>` 和 `<Audio N>` |
| 音频关系 | 描述目标视频自身的聲音 | 在相应镜头或音频阶段引用 `<Audio N>` 并说明信号是被复制还是引用 |

开头示例：

```text
目标视频采用电影感、文学性音乐视频风格，光线柔和，色彩略微去饱和。
[Shot 1] 场景在拥挤的城市街道展开...
[Shot 2] 00:09.000 时，镜头切到极端特写...
```

对于生成任务，`detailed_description` 通常为 350-500 英文单词。对话密集型内容优先适配完整的口语时间线，而非机械达到字数。视频剪辑描述随源视频的复杂度缩放，不必遵循生成任务的范围。单镜头不自动证明更短的描述合理；根据各镜头的信息负载分配细节。

### 5.3 在镜头中使用引用标签

在重要 `<Subject N>` 首次清晰出现时，在镜头中实际可见的内容范围内描述其引用特征、画面中的位置和当前动作。在后续镜头中继续使用相同标签，无需重新定义标签代表的内容。

对具体帧锚点使用自然措辞：

```text
镜头从 <Picture 1> 开始
镜头的关键帧对应 <Picture 2>
镜头结束于 <Picture 3>
```

剪辑或续写原始视频时，在其源状态、结构或续写关系适用处自然地引用 `<Video N>`。在音频关系活跃的镜头或语义阶段引用 `<Audio N>`。

### 5.4 说话人、音频源与对话

基本说话人 ID 和 `<d>` 格式遵循 T2VA。当引用主体实际开口说话时，保留视觉引用标签和说话人 ID：

```text
<Subject 2> (S1) 转向女性说道，<d>[English] Last summer, I went to my grandfather's house. He talked about you.</d>
```

`<Subject N>` 标识引用主体，`(Sx)` 标识实际说话人。当主体说话时，写 `<Subject N> (Sx)`。如果同一主体画外音说话，保持相同形式并标记为 `off-screen`。当说话人不与已定义主体对应时，使用稳定的声音描述后跟 `(Sx)`。

当语言内容仅是直接复用 BGM 或完整配乐中的提示，且没有人物、角色、旁白者或其他独立声音源实际产生时，使用 `<Audio N>` 作为可听声源，不额外发明 `(Sx)`。如果有具体人物、角色、旁白者或其他独立声音源产生声音，为该源分配并复用 `(Sx)`：

```text
当 <Audio 1> 到达短语 <d>[English] I'm lonely lonely lonely lonely lonely I'm lonely</d> 时，<Subject 1> 执行对应手势但不成为独立声音源。
```

当参考音频中的对话、旁白或歌词被直接复用，或输入提示明确要求重新表演时，在 `<d>` 内保留准确的源词和原始语言。对无法辨识的片段写 `[unclear]`，而不是猜测或改述。将标点标准化为表达句子所需的基本书面标记，如 `,`、`.`、`?` 和 `!`；移除重复波浪线、表情符号、项目符号和重复或装饰性标点。完整陈述句、问句和感叹句分别在 `</d>` 前以 `.`、`?` 或 `!` 结尾。

当仅引用音色、节奏、情感或表达方式时，不要将参考音频中的原始对话带入目标视频。

按目标视频中实际发声事件的顺序分配 `(Sx)` 一次。在 `detailed_description` 中的每个实际发声事件处复用对应 ID；在 `subject_definitions` 中绑定到目标说话人的 `<Audio N>` 定义也复用相同 `(Sx)`，但从不独立分配新 ID。不要在 `retention_analysis` 中写 `(Sx)`。仅存在于直接复用 BGM 或完整配乐中的语言提示使用 `<Audio N>`；由具体人物、角色、旁白者或其他独立声音源实际产生的声音使用 `(Sx)`。

## 6. `overall_soundscape` 和 `non_diegetic_music`

这两个声音类别的定义遵循视频提示编写指南（T2VA / I2VA / FL2VA / L2VA）。

`overall_soundscape` 总结整个视频的环境氛围音和物理声音。对话、歌唱和与特定镜头同步的声音事件保留在 `detailed_description` 中：

```text
overall_soundscape: 安静的室内房间底噪和低频通风嗡嗡声贯穿整个视频。
```

`non_diegetic_music` 描述角色无法听到、仅供观众听到的背景音乐。存在音乐时，说明其乐器配置、速度和动态发展：

```text
non_diegetic_music: 克制的独奏钢琴配乐，慢速，底层有持续的低音大提琴，无渐强。
```

使用参考音频时，仅在匹配可听层的部分说明其复制或引用关系：环境音和音效属于 `overall_soundscape`，而仅供观众的配乐属于 `non_diegetic_music`。如果同一音频提供两类内容，在各部分分别描述相应关系：

```text
overall_soundscape: 从 <Audio 1> 复制的环境音层贯穿目标视频。
non_diegetic_music: <Audio 2> 被直接复用作完整的仅供观众配乐。
```

完整对话和歌词仅在 `detailed_description` 的 `<d>` 内编写；不要在这两部分中重复。

## 7. 完整示例

<details>
<summary>显示完整示例</summary>

```text
subject_definitions:
<Subject 1> 是 <Picture 1> 中的咖啡馆环境，有裸砖墙、橙色簇绒沙发配花纹靠垫、霓虹灯标志和木质咖啡桌。
<Subject 2> 是 <Picture 2>、<Picture 3> 和 <Picture 4> 中蓬松的白色萨摩耶，浓密白毛、尖耳朵、黑鼻子和卷曲尾巴。
<Subject 3> 是 <Video 1> 中的年轻金发女性，长金发，浅粉色纽扣衬衫，袖子卷起。
<Subject 4> 是 <Video 2> 中的年轻男性，短卷棕发，深灰色连帽衫带抽绳。
<Audio 1> 是 <Subject 3> (S1) 的音色参考，包含英语口语人声层。

summary:
[reference generation + audio reference] 目标视频展示 <Subject 3> 在 <Subject 1> 中吃饼干。<Subject 4> 带着 <Subject 2> 入场，狗扑向饼干。三镜头交流使用 <Audio 1> 作为 <Subject 3> 的音色参考，以罐头观众笑声结束。

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - 裸砖墙、橙色簇绒沙发、花纹靠垫、霓虹灯标志和木质咖啡桌被保留。
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - 萨摩耶的浓密白毛、尖耳朵、黑鼻子和卷曲尾巴被保留。
<Subject 3> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - 金发女性的身份、长发和浅粉色衬衫被保留。
<Subject 4> (appears in [Shot 1], [Shot 2]): fully_preserved - 年轻男性的短卷棕发和深灰色连帽衫被保留。
<Audio 1>: reference - 其人声音色引导 <Subject 3> 的对话表达，不复制原始信号。

detailed_description:
目标视频采用写实多机位情景喜剧风格，室内暖光照明。
[Shot 1] 中景建立 <Subject 1>，咖啡馆有裸砖墙、橙色簇绒沙发、花纹靠垫、霓虹灯标志和木质咖啡桌。<Subject 3> (S1)，长金发、浅粉色纽扣衬衫卷袖的年轻女性，坐在沙发上拿着巧克力豆饼干。从左侧，<Subject 4>，短卷棕发、深灰色连帽衫带抽绳的年轻男性，牵着 <Subject 2>（浓密白毛、尖耳朵、黑鼻子、卷曲尾巴的萨摩耶）的牵引绳入场。狗扑向饼干拉紧牵引绳。<Subject 3> (S1) 猛地缩手，用从 <Audio 1> 参考的清晰年轻音色，带着轻微恼怒喊道，<d>[English] Hey! Watch your dog!</d> 她闭上嘴护住饼干，<Subject 4> 把狗拉回。
[Shot 2] 00:03.000 时，镜头切到 <Subject 4> (S2) 的特写，Shot 1 中深灰色连帽衫的年轻男性，坐在沙发上 <Subject 3> 旁边，将 <Subject 2> 稳稳抱在怀中。<Subject 4> (S2) 用随性的年轻男声，带着调侃语气和轻松对话节奏说，<d>[English] He just likes cookies more than me.</d> 他闭嘴露出歉疚的微笑，抚摸狗浓密的白毛。
[Shot 3] 00:05.000 时，镜头切到 <Subject 3> (S1) 的特写，Shot 1 中浅粉色衬衫的金发女性。她的恼怒缓和下来，看向萨摩耶。<Subject 3> (S1) 用从 <Audio 1> 参考的相同清晰年轻音色，带着愉悦的语调回答，<d>[English] Well, he has good taste at least.</d> 她微笑举起饼干做一个小型祝酒手势。经典罐头观众笑声在台词结束后立即开始，持续到最后帧。

overall_soundscape:
柔和的室内咖啡馆环境底噪贯穿整个场景。

non_diegetic_music:
N/A
```

</details>
