"""Validate annotation syntax, split, PDF/sidecar hashes and reference quotes."""
import argparse
from eval.corpus import load_corpus


def main(argv=None):
    parser = argparse.ArgumentParser(description='Проверить gold и исходные PDF без сети')
    parser.add_argument('--gold', default='eval/gold')
    parser.add_argument('--manifest', default='eval/manifest.json')
    parser.add_argument('--assets-root', default='eval')
    parser.add_argument('--split', choices=['dev', 'test'])
    args = parser.parse_args(argv)
    try:
        issues, _ = load_corpus(args.gold, args.assets_root, args.manifest, args.split)
        drafts = sum(not i.gold.approved for i in issues)
        print(f'Формат/байты/цитаты: OK; выпусков {len(issues)}, черновиков {drafts}, unscorable полей {sum(f.unscorable for i in issues for f in i.gold.fields.values())}')
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'Ошибка gold: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
