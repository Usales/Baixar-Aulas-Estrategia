# Baixar Aulas Estratégia

Aplicação em Python (GUI com CustomTkinter) para organizar e baixar aulas em PDF ou vídeo da plataforma Estratégia Concursos, usando Playwright para automação do navegador.

## Requisitos

- Python 3.10 ou superior (recomendado 3.11+)
- Windows (o projeto usa caminhos típicos de cache do Playwright no perfil do usuário)

## Instalação

```powershell
cd BaixarAulasEstrategia
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Configuração

1. Copie `config.example.json` para `config.json`.
2. Edite `config.json` com seu e-mail e senha da plataforma, pastas de download e tipo (`pdf` ou vídeo conforme suportado pela interface).

Não commite `config.json`: ele está no `.gitignore` para evitar vazar credenciais.

Os arquivos `course-urls.json`, `meus-cursos-mapeados.json` e `progress.json` são gerados ou preenchidos pelo uso do programa e também ficam fora do Git.

## Execução

```powershell
python main.py
```

## Licença e uso

Use apenas com conta própria e em conformidade com os termos de uso da Estratégia Concursos. Este repositório é uma ferramenta de automação pessoal; o autor não se responsabiliza por uso indevido.
