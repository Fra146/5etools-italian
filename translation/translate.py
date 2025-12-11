#!/usr/bin/env python3
import argparse
import glob
import json
import os
import re
import sys
import time
import traceback

from deep_translator import GoogleTranslator as DeepTranslator

# Languages mapping
supported_languages = {"it": "it"}
# This regex matches tags like {tag}, {tag|option}, etc.
TAG_REGEX = r"{.*?}"

# Global counters
todoCharCounter = 0
maxRuntime = 0
startTime = time.time()
disableCacheSave = False
currentFileIndex = 0
totalFilesCount = 0

# --- Progress Bar Helpers ---


def clear_progress_bar():
    """Clears the current line (where the progress bar is displayed) by printing spaces."""
    # Move cursor to the beginning of the line, print spaces, then move back.
    if sys.stdout.isatty():
        sys.stdout.write("\r" + " " * os.get_terminal_size().columns)
        sys.stdout.flush()


def print_progress_bar(
    current: int, total: int, prefix: str = "Progress", bar_length: int = 50
):
    """Prints a progress bar anchored using \r to overwrite the line."""
    if total == 0:
        return

    percent = current / total
    hashes = "#" * int(round(percent * bar_length))
    spaces = "-" * (bar_length - len(hashes))

    # Format the progress bar string
    progress_str = (
        f"{prefix}: [{hashes}{spaces}] {percent * 100:.1f}% ({current}/{total})"
    )

    # Use \r to move the cursor to the beginning of the line and overwrite previous content
    sys.stdout.write(f"\r{progress_str}")
    sys.stdout.flush()


# ----------------------------


# --- Retry Constants for Stability ---
MAX_RETRIES = 5
BASE_DELAY = 1.0
# -------------------------------------


class TranslatorService:
    # (TranslatorService remains mostly the same, only minor adjustments for logging)
    def __init__(self, language_code: str, cacheFile: str):
        self._target_lang = supported_languages.get(language_code)
        if not self._target_lang:
            raise ValueError(f"Unsupported language code: {language_code}")

        self._cacheFile = cacheFile
        self._cacheDirty = False
        self._cacheData = {}
        self.charCount = 0
        self.cachedCharCount = 0
        self.current_file_name = os.path.basename(cacheFile).replace(".json", "")

        if os.path.exists(cacheFile):
            try:
                with open(cacheFile, encoding="utf-8") as f:
                    self._cacheData = json.load(f)
                # print(f"Loaded existing cache from '{cacheFile}'.") # Suppressing this for cleaner output
            except json.JSONDecodeError:
                print(f"Warning: Cache file '{cacheFile}' is corrupt. Starting fresh.")
                self._cacheData = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global disableCacheSave
        global suppressErrors
        if not disableCacheSave:
            self.cacheSync()
        else:
            print(
                f"\nCache saving is disabled; changes to cache not saved to {self._cacheFile}."
            )

        if (
            exc_type is not None and not suppressErrors
        ):  # If something happened and we're suppressing errors
            print(f"\n--- An error occurred ({exc_type.__name__}) ---")
            traceback.print_exception(exc_type, exc_val, exc_tb)

    def cacheSync(self):
        """Writes the current cache data to disk."""

        if self._cacheDirty:
            # Clear bar before printing file save message
            clear_progress_bar()
            print(f"\nSaving cache to {self._cacheFile}")
            try:
                temp_file = f"{self._cacheFile}.swp"
                with open(temp_file, mode="w", encoding="utf-8") as f:
                    json.dump(self._cacheData, f, indent="\t", ensure_ascii=False)
                os.rename(temp_file, self._cacheFile)
                self._cacheDirty = False
            except Exception as e:
                print(f"Error saving cache: {e}")

    def cacheSet(self, key, value):
        global disableCacheSave
        if disableCacheSave:
            return

        self._cacheData[key] = value
        self._cacheDirty = True

    def cacheGet(self, key):
        return self._cacheData.get(key)

    # --- Tag Handling Functions (Unchanged) ---
    def links2tags(self, text: str) -> tuple[str, list]:
        links = []
        count = 0
        for match in re.finditer(TAG_REGEX, text):
            links.append(match.group(0))
            text = text.replace(match.group(0), f"(%{count}%)", 1)
            count += 1
        return text, links

    def tags2links(self, text: str, links: list) -> str:
        for idx, link in enumerate(links):
            text = text.replace(f"(%{idx}%)", link, 1)
        return text

    def translate(self, text: str) -> str:
        """Translates text, using cache or deep-translator, with retry logic."""

        noVars = re.sub(TAG_REGEX, "", text)
        if len(re.sub(r"[\d\s()\[\].,_-]+", "", noVars)) < 5:
            return text

        # 1. Check Cache
        cached_result = self.cacheGet(text)
        if cached_result is not None:
            self.cachedCharCount += len(text)
            return cached_result

        # 🚀 CACHE MISS INDICATION 🚀
        clear_progress_bar()
        print(
            f"CACHE MISS! In file **{self.current_file_name}** for text: '{text[:80].replace(os.linesep, ' ')}...'"
        )
        print_progress_bar(currentFileIndex, totalFilesCount, prefix="Total Progress")
        # --------------------------------

        # 2. Check Runtime Limit
        global maxRuntime, startTime
        if maxRuntime != 0 and time.time() - startTime > maxRuntime:
            raise Exception("Maximum runtime exceeded - aborting")

        self.charCount += len(text)

        # 3. Apply Tag Handling
        translate_text, links = self.links2tags(text)

        last_error = None

        # --- Retry Loop Implementation ---
        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                clear_progress_bar()
                print(
                    f"Retrying translation (Attempt {attempt + 1}/{MAX_RETRIES}) after {delay:.1f}s delay..."
                )
                print_progress_bar(
                    currentFileIndex, totalFilesCount, prefix="Total Progress"
                )
                time.sleep(delay)

            clear_progress_bar()
            print(f"DeepTranslator Call ({len(text)} chars): '{text[:60]}...'")
            print_progress_bar(
                currentFileIndex, totalFilesCount, prefix="Total Progress"
            )

            try:
                translator = DeepTranslator(source="en", target=self._target_lang)
                translated_text = translator.translate(translate_text)
                break

            except Exception as e:
                last_error = e
                clear_progress_bar()
                print(f"\n!!! DeepTranslator Error on attempt {attempt + 1}: {e} !!!")
                print_progress_bar(
                    currentFileIndex, totalFilesCount, prefix="Total Progress"
                )
        # --- End Retry Loop ---

        if "translated_text" not in locals():
            clear_progress_bar()
            print(
                f"\n!!! Translation failed after {MAX_RETRIES} attempts. Returning untranslated text. !!!"
            )
            if last_error:
                traceback.print_exc(limit=5)
            print_progress_bar(
                currentFileIndex, totalFilesCount, prefix="Total Progress"
            )
            return text

        # 4. Restore Tags
        translated_text = self.tags2links(translated_text, links)

        # Store in cache and return
        clear_progress_bar()
        print(f" -> Translated: '{translated_text[:60]}...'\n")
        self.cacheSet(text, translated_text)
        print_progress_bar(currentFileIndex, totalFilesCount, prefix="Total Progress")
        return translated_text


def translate_data(translator: TranslatorService, data):
    # (translate_data remains completely unchanged)
    if type(data) is list:
        for element in data:
            translate_data(translator, element)
    elif type(data) is dict:
        for k, v in data.items():
            if k in ["entry", "effect"] and type(v) is str:
                data[k] = translator.translate(v)
            elif k == "other" and type(v) is dict:
                for section, items in v.items():
                    for idx, item in enumerate(items):
                        data[k][section][idx] = translator.translate(item)
            elif (
                k
                in [
                    "entries",
                    "items",
                    "rows",
                    "headerEntries",
                    "reasons",
                    "other",
                    "lifeTrinket",
                ]
                and type(v) is list
            ):
                if k == "items" and ("type" not in data or data["type"] != "list"):
                    continue

                for idx, entry in enumerate(v):
                    if type(entry) is list:
                        for elidx, el in enumerate(entry):
                            if type(el) is str and len(el) > 2:
                                data[k][idx][elidx] = translator.translate(el)

                    if type(entry) is str:
                        data[k][idx] = translator.translate(entry)
                    else:
                        translate_data(translator, entry)
            else:
                translate_data(translator, v)


def translate_file(language: str, fileName: str, writeJSON: bool):
    """Sets up paths, loads file, translates content, and saves output."""

    # Define file paths
    cache_file = fileName.replace("data/", f"translation/cache/{language}/")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    output_file = fileName.replace("data/", f"data.{language}/")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    data = {}

    # Initialize the translator manager with caching
    with TranslatorService(language, cache_file) as translator:
        translator.current_file_name = os.path.basename(fileName)

        try:
            with open(fileName, encoding="utf-8") as f:
                data = json.load(f)

            # Start translation traversal
            translate_data(translator, data)

        except Exception as e:
            clear_progress_bar()
            print(f"\n!!! File/Runtime Error in {fileName}: {repr(e)} !!!")
            print_progress_bar(
                currentFileIndex, totalFilesCount, prefix="Total Progress"
            )

        clear_progress_bar()
        print(
            f"Cached chars: {translator.cachedCharCount:,}\tDeepTranslator chars: {translator.charCount:,}"
        )
        print_progress_bar(currentFileIndex, totalFilesCount, prefix="Total Progress")

        global todoCharCounter
        todoCharCounter += translator.charCount

    # Write the output file
    if writeJSON:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent="\t", ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Translate json data using deep-translator (Google Translator)"
    )
    parser.add_argument(
        "--language",
        type=str,
        required=True,
        help="Target language code (e.g., it, es, de).",
    )
    parser.add_argument(
        "--translate",
        type=bool,
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable writing the translated JSON output files.",
    )
    parser.add_argument(
        "--maxrun",
        type=int,
        default=0,
        help="Maximum runtime in seconds before aborting (0 for unlimited).",
    )
    parser.add_argument(
        "--nocache",
        action="store_true",
        help="Disable writing new translations back to the cache file (still loads/retrieves from cache).",
    )
    parser.add_argument(
        "--suppress-errors",
        action="store_true",
        help="Suppress error messages when interrupting.",
    )
    parser.add_argument(
        "files",
        type=str,
        nargs="+",
        help="List of JSON file patterns (e.g., data/spells/*.json) to translate.",
    )
    args = parser.parse_args()
    maxRuntime = args.maxrun

    disableCacheSave = args.nocache
    suppressErrors = args.suppress_errors

    if args.language.lower() not in supported_languages:
        raise Exception(
            f"Unsupported language {args.language} - Valid are: {supported_languages.keys()}"
        )

    all_files = []
    for pattern in args.files:
        found_files = glob.glob(pattern)
        if not found_files:
            print(f"Warning: No files found matching pattern '{pattern}'. Skipping.")
        all_files.extend(found_files)

    if not all_files:
        print(
            "\nError: No valid input files were found after checking all patterns. Please check your file paths."
        )
        sys.exit(1)

    # 🌟 Initialize global file counters 🌟
    totalFilesCount = len(all_files)
    currentFileIndex = 0

    # Print the initial, empty progress bar
    print_progress_bar(currentFileIndex, totalFilesCount, prefix="Total Progress")
    # Main translation loop
    for file in all_files:
        currentFileIndex += 1  # Increment file counter immediately

        # 1. Clear the bar so the new file message appears above it
        clear_progress_bar()

        # 2. Display file progress
        print(
            f"\n--- Processing File {currentFileIndex}/{totalFilesCount} ({((currentFileIndex) / totalFilesCount) * 100:.1f}%): {file} ---"
        )

        if file.startswith("data/generated"):
            continue

        # 3. Print the progress bar again *after* the file header is printed
        print_progress_bar(currentFileIndex, totalFilesCount, prefix="Total Progress")

        translate_file(args.language.lower(), file, args.translate)
    print(f"\n--- Translation Complete ---")

    # Final output
    clear_progress_bar()
    print(f"Total files processed: {currentFileIndex}/{totalFilesCount}")
    print(f"Total characters translated (via DeepTranslator): {todoCharCounter:,}")
