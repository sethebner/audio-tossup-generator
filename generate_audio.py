import argparse
import colorama
import csv
import json
import pathlib
import pytube

from pydub import AudioSegment
from tqdm import tqdm

VIDCACHE_DIR = "vidcache"
ERR_VIDEO_PATH = "_err_video_path"

_CSV_QUESTION_ID_KEY = 'Question ID'
_CSV_DESCRIPTION_KEY = 'Description'
_CSV_LINK_KEY = 'Link'
_CSV_TIME_START_KEY = 'Start at (sec)'
_CSV_TIME_LENGTH_KEY = 'Length (sec)'
_CSV_QUESTION_PREFIX = 'Q:'
_CSV_ANSWER_PREFIX = 'A:'

def parse_args():
	parser = argparse.ArgumentParser()
	parser.add_argument('--input-file', required=True, help='File containing question clues')
	parser.add_argument('--qs', nargs='+', required=False, help='Generate questions with specified ids')
	parser.add_argument('--all', action='store_true', help='Generate all questions')
	parser.add_argument('--output-dir', required=True, help='Directory where to put audio files')
	parser.add_argument('--overwrite', action='store_true', help='Overwrites audio files')

	args = parser.parse_args()

	if bool(args.qs) and bool(args.all):
		raise ValueError(f'Specifying individual questions is incompatible with specifying all questions. Choose either --qs or --all.')

	return args

def generate_empty_question():
	return {'qid': None, 'question': '', 'answer': '', 'clues': []}

def parse_csv(f):
	data = []
	csv_file = csv.DictReader(f)
	current_question = generate_empty_question()
	for line in csv_file:
		is_blank_line = all(v == '' for v in line.values())
		has_different_question_id = (current_question['qid'] != None) and (line[_CSV_QUESTION_ID_KEY] != '') and (line[_CSV_QUESTION_ID_KEY] != current_question['qid'])
		is_end_of_question = is_blank_line or has_different_question_id  # Assume empty line means a break between questions
		if is_end_of_question:
			if current_question == generate_empty_question():
				# Empty question (likely because of multiple blank lines), so no need to retain it 
				continue
			data.append(current_question)
			current_question = generate_empty_question()
			if is_blank_line:
				# Nothing to process, so move on to next line
				continue
			else:
				# There is information in this line, so keep processing it
				pass
		if not current_question['qid']:
			if line[_CSV_QUESTION_ID_KEY].strip() == "":
				raise ValueError('The first line of the question must specify the Question ID.')
			else:
				current_question['qid'] = line[_CSV_QUESTION_ID_KEY]
		if line[_CSV_DESCRIPTION_KEY].startswith(_CSV_QUESTION_PREFIX):
			# Question line
			current_question['question'] = line[_CSV_DESCRIPTION_KEY].replace(_CSV_QUESTION_PREFIX, '', 1).strip()
		elif line[_CSV_DESCRIPTION_KEY].startswith(_CSV_ANSWER_PREFIX):
			# Answer line
			current_question['answer'] = line[_CSV_DESCRIPTION_KEY].replace(_CSV_ANSWER_PREFIX, '', 1).strip()
		else:
			# Clue line
			current_clue = {'description': line[_CSV_DESCRIPTION_KEY], 'link': line[_CSV_LINK_KEY], 'start': int(line[_CSV_TIME_START_KEY]), 'length': int(line[_CSV_TIME_LENGTH_KEY])}
			current_question['clues'].append(current_clue)

	if current_question['clues']:
		# No blank line at end of file to indicate end of question, so end the question here
		data.append(current_question)

	return data

def read_file(input_file):
	file_type = pathlib.Path(input_file).suffix
	with open(input_file, "r") as f:
		if file_type == '.json':
			data = json.load(f)
		elif file_type == '.csv':
			data = parse_csv(f)

	return data

def get_video_path_for_clue(youtube_link):
	youtube_video_id = youtube_link.partition('watch?v=')[-1]
	expected_cached_video_path = pathlib.Path(VIDCACHE_DIR, youtube_video_id)
	if expected_cached_video_path.is_file():
		return expected_cached_video_path
	
	# Download file
	try:
		video_path = pytube.YouTube(youtube_link).streams.filter(
			only_audio=True, progressive=False, subtype='mp4').first().download(VIDCACHE_DIR, youtube_video_id, skip_existing=True)
	except pytube.exceptions.PytubeError as e:
		# print(e)
		# print(f'Link: {youtube_link}')
		return ERR_VIDEO_PATH

	return video_path

def process_clip(video_path, start, length):
	song = AudioSegment.from_file(video_path, "mp4")
	start_time = start*1000
	end_time = start_time + length*1000
	chopped = song[start_time:end_time]
	faded = chopped.fade_in(750).fade_out(750)

	return faded

def main():
	args = parse_args()
	colorama.init(autoreset=True)

	data = read_file(args.input_file)

	texts = []
	for question in data:
		if (not args.all) and (question['qid'] not in args.qs):
			continue

		unique_clues = list(dict.fromkeys([c['description'] for c in question['clues']]))  # deduplicate but retain order
		texts.append({"question_id": question['qid'], "question": question['question'], "answer": question['answer'], "clues": unique_clues})

		question_audio_path = pathlib.Path(args.output_dir, question['qid']).with_suffix('.mp3')
		if question_audio_path.is_file() and not args.overwrite:
			# File exists, so skip
			print(f"({question['qid']}) Already exists. Skipping.")
			continue

		# Collate YouTube files for clues
		clue_clips = []
		for clue in tqdm(question['clues'], desc=f"({question['qid']}) Downloading files"):
			video_path = get_video_path_for_clue(clue['link'])
			if video_path == ERR_VIDEO_PATH:
				print(f"{colorama.Fore.RED}! Error with video path in question {question['qid']} for clue {clue['description']}. No audio for this clue will be generated.{colorama.Style.RESET_ALL}")
				continue
			clue_clips.append({'video_path': video_path, 'start': clue['start'], 'length': clue['length']})

		# Generate audio file
		question_audio = AudioSegment.empty()
		for clip in tqdm(clue_clips, desc=f"({question['qid']}) Generating audio"):
			question_audio += process_clip(clip['video_path'], clip['start'], clip['length'])

		# Export audio file
		if question_audio.duration_seconds <= 0:
			print(f"{colorama.Fore.RED}! Failed to generate audio for question {question['qid']}.{colorama.Style.RESET_ALL}")
			continue

		question_audio.export(question_audio_path, format="mp3")

	# Export text files (question, answer, clues)
	if args.all:
		with open(pathlib.Path(args.output_dir, "questions").with_suffix('.txt'), "w") as f:
			for question_text in texts:
				text = f"{question_text['question_id']}. {question_text['question']}"
				text += '\n\n'
				f.write(text)

		with open(pathlib.Path(args.output_dir, "answers").with_suffix('.txt'), "w") as f:
			for question_text in texts:
				text = f"{question_text['question_id']}. ANSWER: {question_text['answer']}"
				text += '\n'
				for clue in question_text['clues']:
					text += clue
					text += '\n'
				text += '\n'
				f.write(text)

if __name__ == "__main__":
	main()