<h1 align="center">Baixar Aulas Estratégia</h1>

<p align="center"><strong>Ferramenta com interface gráfica para baixar PDFs e vídeos dos seus cursos na Estratégia Concursos, com automação do navegador (Playwright).</strong></p>

---

### Como usar este README (leia primeiro)

No GitHub (e na maioria dos visualizadores de Markdown), **tudo que está em link azul** no [sumário abaixo](#sumario) leva direto à seção. Use o sumário como mapa: se você travou em um passo, abra o link correspondente e siga a lista **na ordem**.

**Regra de ouro:** não pule a parte de [credenciais e segurança](#seguranca-credenciais). Senha em repositório público vira problema sério.

---

<h2 id="sumario">Sumário (links rápidos)</h2>

| Onde você está | Abra este link |
|----------------|----------------|
| Quero saber o que o programa faz | [O que este programa faz](#o-que-faz) |
| O que preciso ter no PC | [Requisitos](#requisitos) |
| Nunca usei Python / terminal | [Do zero: Python e pasta do projeto](#do-zero) |
| Já tenho Python, quero instalar rápido | [Instalação resumida](#instalacao-resumida) |
| O que é cada arquivo JSON | [Arquivos do projeto](#arquivos-projeto) |
| O que cada campo do `config.json` significa | [Configuração campo a campo](#config-campo-a-campo) |
| Como usar a tela do programa | [Como usar a interface](#interface) |
| Deu erro | [Problemas comuns](#troubleshooting) |
| Posso postar no GitHub com senha? | [Segurança](#seguranca-credenciais) |
| Termos legais | [Uso responsável](#uso-responsavel) |

Links extras (também em azul no GitHub):

- [Detalhe: ambiente virtual (venv) — por que usar](#venv-explicado)
- [Detalhe: Playwright e Chromium](#playwright-explicado)
- [Detalhe: pastas de download no Windows](#pastas-windows)
- [Detalhe: tipo de PDF (`pdfType`)](#pdf-type)
- [Detalhe: fila `course-urls.json`](#course-urls)

---

<h2 id="o-que-faz">O que este programa faz (em linguagem simples)</h2>

1. Abre um **navegador controlado pelo programa** (Chromium via Playwright).
2. Acessa o site da **Estratégia Concursos** com **o seu login** (mesmo login que você usa no site).
3. Você monta uma **fila de cursos/matérias** (por URL de “aulas” ou importando de “Meus cursos” / pacotes).
4. O programa **percorre as aulas** e **baixa** o conteúdo como **PDF** ou **vídeo**, conforme você escolhe nas configurações, salvando em pastas que **você define**.

**O que este programa não é:** não é site oficial, não é app da Estratégia, não “libera” curso que você não comprou. Funciona em cima do **que a sua conta já tem direito** de acessar no site.

---

<h2 id="requisitos">Requisitos do computador</h2>

- **Sistema:** Windows (o código usa caminhos típicos de cache do Playwright em `%USERPROFILE%\AppData\Local\...`).
- **Python:** 3.10 ou superior (recomendado **3.11** ou **3.12**).
- **Internet:** estável o suficiente para login e downloads grandes (vídeo pesa muito mais que PDF).
- **Espaço em disco:** depende de quantos cursos e vídeos; reserve vários GB se for baixar muita coisa em vídeo.
- **Conta:** e-mail e senha **válidos** da Estratégia (conta de aluno com os cursos contratados).

Se você não sabe se o Python está instalado, vá para [Do zero: Python e pasta do projeto](#do-zero).

---

<h2 id="do-zero">Do zero: Python, pasta do projeto e terminal (bem detalhado)</h2>

<h3 id="passo-python">Passo 1 — Instalar o Python (se ainda não tiver)</h3>

1. Baixe o instalador em [python.org](https://www.python.org/downloads/windows/) (versão estável recente).
2. Execute o instalador.
3. **Marque a opção** “Add python.exe to PATH” / “Adicionar Python ao PATH” (isso evita 90% dos erros “python não é reconhecido”).
4. Conclua a instalação.

<h3 id="passo-testar-python">Passo 2 — Testar se o Python funciona</h3>

1. Abra o **PowerShell** (menu Iniciar → digite `PowerShell` → abrir).
2. Digite exatamente:

```powershell
python --version
```

3. Se aparecer algo como `Python 3.11.x`, está ok.
4. Se aparecer erro do tipo **não reconhecido**, o PATH não foi configurado: reinstale o Python marcando “Add to PATH”, ou use a opção “Modify” do instalador para corrigir.

<h3 id="passo-pasta">Passo 3 — Entrar na pasta do projeto</h3>

Se você clonou o repositório, a pasta costuma se chamar `Baixar-Aulas-Estrategia` ou `BaixarAulasEstrategia`. No PowerShell, use `cd` com o **caminho real** da pasta no seu PC, por exemplo:

```powershell
cd C:\Users\SEU_USUARIO\Desktop\Baixar-Aulas-Estrategia
```

**Dica:** no Explorer, abra a pasta do projeto, clique na barra de endereço, copie o caminho e cole depois de `cd ` no PowerShell.

---

<h2 id="venv-explicado">Por que criar ambiente virtual (`venv`)</h2>

O `venv` cria uma “caixinha” só deste projeto com as bibliotecas certas. Assim você **não mistura** pacotes de outros projetos e evita conflito de versões.

Sempre que for usar o programa, **ative** o ambiente (comando abaixo). O prompt costuma mostrar `(.venv)` quando está ativo.

---

<h2 id="instalacao-resumida">Instalação (comandos completos)</h2>

Execute **na ordem**, dentro da pasta do projeto:

```powershell
cd CAMINHO\PARA\A\PASTA\DO\PROJETO
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

**Se o PowerShell bloquear scripts** ao ativar o `venv` (política de execução), você pode ver uma mensagem de erro. Nesse caso, em uma sessão **aberta como administrador** (só se você souber o que está fazendo), costuma-se usar:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Depois feche e abra o PowerShell de novo e tente `.\.venv\Scripts\Activate.ps1` outra vez.

---

<h2 id="playwright-explicado">Playwright e Chromium</h2>

- **`pip install -r requirements.txt`** instala a **biblioteca** Playwright no Python.
- **`playwright install chromium`** baixa o **navegador Chromium** que o Playwright usa para abrir o site.

Sem o segundo comando, é comum dar erro ao tentar abrir o navegador automatizado.

---

<h2 id="arquivos-projeto">Arquivos importantes na pasta do projeto</h2>

| Arquivo | Entra no Git? | Para que serve |
|---------|----------------|----------------|
| `main.py` | Sim | Código do programa (interface + lógica). |
| `requirements.txt` | Sim | Lista de dependências Python. |
| `config.example.json` | Sim | Modelo de configuração **sem** sua senha real. |
| `config.json` | **Não** (está no `.gitignore`) | **Sua** configuração real: login, pastas, opções. Você cria a partir do exemplo. |
| `course-urls.json` | **Não** | Fila de URLs de cursos (matérias) para download. O app lê e grava. |
| `meus-cursos-mapeados.json` | **Não** | Catálogo gerado a partir de “Meus cursos” / assinaturas (título + link). |
| `progress.json` | **Não** | Controle do que já foi baixado (para não refazer tudo do zero). |

Se você apagou `course-urls.json` por engano, o programa pode recriar conforme o fluxo da interface, mas **perde a fila** que estava salva.

---

<h2 id="config-campo-a-campo">Configuração: `config.json` campo a campo</h2>

1. Copie `config.example.json` para um novo arquivo chamado **`config.json`** (mesma pasta que `main.py`).
2. Edite com um editor de texto (VS Code, Notepad++, etc.). **Salve em UTF-8** se o editor perguntar.

<h3 id="campos-gerais">Campos gerais</h3>

- **`email`**: e-mail da sua conta na Estratégia.
- **`senha`**: senha da conta. Trate como **dado sensível** (veja [Segurança](#seguranca-credenciais)).
- **`downloadType`**: `"pdf"` ou `"video"` — define o modo principal de download usado pelo fluxo automatizado.
- **`headless`**: `true` = navegador **sem janela visível** (mais “limpo”); `false` = você **vê** o navegador abrindo (ótimo para **depurar** login ou erro de página).
- **`minimizeAfterLogin`**: quando `true`, tende a minimizar após login; com `false` você acompanha melhor o que está acontecendo na janela do navegador.

<h3 id="pdfconfig">Bloco `pdfConfig`</h3>

- **`pastaDownloads`**: pasta onde os **PDFs** serão salvos. Use caminho absoluto, por exemplo `D:/Estrategia/PDFs` ou `C:/Users/.../Downloads/EstrategiaPDF`. Evite caminhos muito exóticos com permissão restrita até testar.
- **`pdfType`**: número de **1 a 5**, ligado ao “Tipo de PDF” na interface. Significado na prática (nomes iguais aos da tela):
  - **1** — Simplificado  
  - **2** — Original  
  - **3** — Marcado  
  - **4** — Todos (+ extras)  
  - **5** — Simplificado + Marcado  
- **`concurrentMatriculas`**: quantas matérias o modo PDF tenta processar **em paralelo**. O código **limita entre 2 e 10**; valor padrão sugerido no exemplo é **6**. Aumentar demais pode pesar no PC e no site; diminuir pode deixar mais lento porém mais estável.

<h3 id="videoconfig">Bloco `videoConfig`</h3>

- **`pastaDownloads`**: pasta dos **vídeos**.
- **`resolucaoEscolhida`**: uma entre **`720p`**, **`480p`**, **`360p`** (mesmas opções da interface).

---

<h2 id="pastas-windows">Pastas no Windows (barra / ou \\)</h2>

No `config.json` você pode usar **`D:/Pastas/Subpasta`** (barra normal) — costuma funcionar bem no Python no Windows. Se usar `\`, lembre que em JSON a barra invertida é caractere especial: prefira **`/`** ou dobre a barra (`\\`) se for obrigatório.

Antes de rodar download grande, **confirme** que a pasta existe ou que o disco tem espaço.

---

<h2 id="interface">Como usar a interface (menu lateral)</h2>

O programa se chama na barra de título algo como **“Estratégia Downloader Pro”**. À esquerda há botões; abaixo, o que cada um serve **em termos práticos**:

<h3 id="tela-inicio">Início</h3>

- Botão grande para **iniciar downloads** com base na fila e nas configurações já salvas.
- Barra de progresso acompanha o andamento geral.

**Antes de clicar:** configure login e pastas em [Configurações](#tela-config), e tenha URLs na fila ([Matérias](#tela-materias) / [Pacotes](#tela-pacotes) / [Minhas matrículas](#tela-matriculas)).

<h3 id="tela-pacotes">Pacotes</h3>

- Você cola uma URL do tipo **lista de cursos** (“Meus cursos” no dashboard) ou página de **pacote**.
- O programa tenta **ler os cards** e **enfileirar** cada matéria automaticamente em `course-urls.json`.

Se a URL estiver errada (página que não lista cursos), nada útil entra na fila.

<h3 id="tela-matriculas">Minhas matrículas</h3>

- Conecta ao site (já logado pelo fluxo do app), percorre **“Meus cursos”** e **“Assinaturas”**, monta um catálogo com título + link e grava em **`meus-cursos-mapeados.json`**.
- A partir daí você usa os botões da tela para **mandar cursos para a fila** de download (integração com `course-urls.json`).

<h3 id="tela-materias">Matérias Individuais</h3>

- Para quem quer **colar manualmente** URLs de páginas de aulas de um curso específico (uma matéria por vez ou várias linhas, conforme a própria tela permitir).
- Útil quando você sabe o link exato e não quer passar pelo fluxo de pacote.

<h3 id="tela-logs">Logs</h3>

- Mostra mensagens do processo (erros de rede, timeout, etc.).
- Quando algo falhar, **copie as últimas linhas** do log antes de pedir ajuda em issue — isso acelera muito o diagnóstico.

<h3 id="tela-config">Configurações</h3>

- Tudo que está no `config.json`, porém visual: e-mail, senha, tipo de download, headless, pastas, tipo de PDF, resolução de vídeo, etc.
- Sempre que mudar algo importante, use **Salvar Configurações** e confira se o `config.json` na pasta foi atualizado.

---

<h2 id="course-urls">Fila de URLs (`course-urls.json`)</h2>

Cada item da lista é uma URL de curso no formato de página de aulas, em geral contendo:

- `/app/dashboard/cursos/NUMERO/aulas` **ou**
- padrão clássico com `/cursos/NUMERO/...`

O programa já tem funções para **normalizar** URL (tirar espaço, aspas, caracteres invisíveis). Mesmo assim, prefira **copiar o link direto do navegador** logado.

---

<h2 id="executar">Como executar o programa</h2>

Com o `venv` **ativado** e na pasta do projeto:

```powershell
python main.py
```

Se aparecer erro de módulo não encontrado, quase sempre é porque você **não ativou** o `.venv` ou **não rodou** `pip install -r requirements.txt` dentro desse ambiente.

---

<h2 id="troubleshooting">Problemas comuns (checklist)</h2>

1. **`python` não é reconhecido** → Python não está no PATH; reinstale/corrija PATH ([Passo 2](#passo-testar-python)).
2. **Erro ao importar `playwright`** → Rode `pip install -r requirements.txt` com o venv ativo.
3. **Erro ao lançar navegador / Chromium** → Rode `playwright install chromium`.
4. **Login não completa** → Ponha `headless` em `false` e `minimizeAfterLogin` em `false` para **ver** o navegador; confira e-mail/senha; confira se o site não pediu captcha ou verificação extra.
5. **Pasta de download vazia** → Confira o caminho em `pdfConfig.pastaDownloads` ou `videoConfig.pastaDownloads`; confira permissão de escrita no disco.
6. **Muito lento ou travando** → Reduza `concurrentMatriculas`; feche outros programas pesados; teste outra rede.

---

<h2 id="seguranca-credenciais">Segurança e credenciais (leia de verdade)</h2>

- **Nunca** commite `config.json` com senha em repositório **público**. Qualquer pessoa pode ler o histórico do Git mesmo que você apague depois.
- Este repositório já lista `config.json` no **`.gitignore`** justamente por isso. Use apenas **`config.example.json`** como modelo.
- Se em algum momento você **subiu** senha para o GitHub por engano:
  1. Troque a **senha** do site da Estratégia **imediatamente**.
  2. Considere trocar também e-mail/recuperação se houver risco.
  3. No GitHub, use **Secret scanning** / suporte para rotacionar tokens se vazou mais coisa além disso.

---

<h2 id="uso-responsavel">Uso responsável e termos</h2>

Use **somente** com **sua própria conta** e de acordo com os **termos de uso** da Estratégia Concursos e da lei de direito autoral aplicável. O repositório é uma ferramenta de automação **pessoal**; quem usa responde pelo uso. O mantenedor não se responsabiliza por uso indevido, redistribuição de conteúdo protegido ou violação de contrato com a plataforma.

---

<h2 id="contribuir">Contribuições, issues e melhorias</h2>

Se for reportar bug, inclua: versão do Python, versão do Playwright (`pip show playwright`), sistema operacional, trecho relevante dos **Logs** da aplicação, e o que você esperava que acontecesse. Sem isso, fica adivinhação.

---

<p align="center"><strong>Bom estudo — e configure a fila com calma antes de largar o download rodando.</strong></p>
