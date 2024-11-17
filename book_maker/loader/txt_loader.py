import sys
from pathlib import Path

from book_maker.utils import prompt_config_to_kwargs

from .base_loader import BaseBookLoader


class TXTBookLoader(BaseBookLoader):
    def __init__(
            self,
            txt_name,
            model,
            key,
            resume,
            language,
            model_api_base=None,
            is_test=False,
            test_num=5,
            prompt_config=None,
            single_translate=False,
            context_flag=False,
            temperature=1.0,
    ) -> None:
        self.txt_name = txt_name
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            temperature=temperature,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.p_to_save = []
        self.bilingual_result = []
        self.bilingual_temp_result = []
        self.test_num = test_num
        self.batch_size = 10
        self.single_translate = single_translate

        try:
            with open(f"{txt_name}", encoding="utf-8") as f:
                self.origin_book = f.read().splitlines()

        except Exception as e:
            raise Exception("can not load file") from e

        self.resume = resume
        self.bin_path = f"{Path(txt_name).parent}/.{Path(txt_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace() or len(text) == 0

    def _make_new_book(self, book):
        pass

    def make_bilingual_book(self):
        index = 0
        p_to_save_len = len(self.p_to_save)

        try:
            sliced_list = [
                self.origin_book[i: i + self.batch_size]
                for i in range(0, len(self.origin_book), self.batch_size)
            ]
            for i in sliced_list:
                # fix the format thanks https://github.com/tudoujunha
                batch_text = "\n".join(i)
                if self._is_special_text(batch_text):
                    continue
                if not self.resume or index >= p_to_save_len:
                    try:
                        translated_text = self.translate_model.translate(batch_text)
                    except Exception as e:
                        print(e)
                        raise Exception("Something is wrong when translate") from e
                    self.p_to_save.append(translated_text)
                    if self.single_translate:
                        self.bilingual_result.append(translated_text)
                    else:  # 中英双语对照
                        bi_text_list = self.alternate_print(batch_text, translated_text)
                        self.bilingual_result.append("\n".join(bi_text_list))
                index += self.batch_size
                if self.is_test and index > self.test_num:
                    break

            self.save_file(
                f"{Path(self.txt_name).parent}/{Path(self.txt_name).stem}_bilingual.txt",
                self.bilingual_result,
            )

        except (KeyboardInterrupt, Exception) as e:
            print(e)
            print("you can resume it next time")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)

    def _save_temp_book(self):
        index = 0
        sliced_list = [
            self.origin_book[i: i + self.batch_size]
            for i in range(0, len(self.origin_book), self.batch_size)
        ]

        for i in range(len(sliced_list)):
            batch_text = "".join(sliced_list[i])
            self.bilingual_temp_result.append(batch_text)
            if self._is_special_text(self.origin_book[i]):
                continue
            if index < len(self.p_to_save):
                self.bilingual_temp_result.append(self.p_to_save[index])
            index += 1

        self.save_file(
            f"{Path(self.txt_name).parent}/{Path(self.txt_name).stem}_bilingual_temp.txt",
            self.bilingual_temp_result,
        )

    def _save_progress(self):
        try:
            with open(self.bin_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.p_to_save))
        except:
            raise Exception("can not save resume file")

    def load_state(self):
        try:
            with open(self.bin_path, encoding="utf-8") as f:
                self.p_to_save = f.read().splitlines()
        except Exception as e:
            raise Exception("can not load resume file") from e

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
        except:
            raise Exception("can not save file")

    # 制作双语文本
    def alternate_print(self, raw_text, trans_text):
        english_lines = [line for line in raw_text.splitlines() if line.strip()]
        chinese_lines = [line for line in trans_text.splitlines() if line.strip()]
        eng_len = len(english_lines)
        ch_len = len(chinese_lines)
        if eng_len != ch_len:
            print(f"文件行数不一致：raw_text 有{eng_len}行，trans_text 有{ch_len}行。")

        max_length = max(eng_len, ch_len)
        bi_lines = []
        for i in range(max_length):
            if i < eng_len:
                bi_lines.append(english_lines[i])
            if i < ch_len:
                bi_lines.append(chinese_lines[i])
        return bi_lines
