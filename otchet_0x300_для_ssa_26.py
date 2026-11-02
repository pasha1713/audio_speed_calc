from datetime import datetime, timedelta, timezone
import math

def haversine(lat1, lon1, lat2, lon2):
    """Рассчитывает расстояние между двумя точками на Земле в метрах"""
    # Переводим в радианы
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Формула гаверсинуса
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Радиус Земли в метрах
    r = 6371000  # радиус в метрах
    return round(c * r, 2)  # округляем до сантиметров

def convert_to_msk(utc_datetime_str):
    # Преобразуем строку в объект datetime
    naive_datetime = datetime.strptime(utc_datetime_str, '%Y:%m:%d %H:%M:%SZ')
    # Создаём timezone-aware объект (UTC)
    utc_datetime = naive_datetime.replace(tzinfo=timezone.utc)
    # Конвертируем в MSK (UTC+3)
    msk_datetime = utc_datetime.astimezone(timezone(timedelta(hours=3)))
    # Форматируем обратно в строку
    return msk_datetime.strftime('%Y-%m-%d %H:%M:%S MSK')

def parse_packet_report(file_path):
    pid_frames = {
        '0x0000': {'counter': 0, 'frames': {}},
        '0x0040': {'counter': 0, 'frames': {}},
        '0x0100': {'counter': 0, 'frames': {}},
        '0x0200': {'counter': 0, 'frames': {}},
        '0x0201': {'counter': 0, 'frames': {}},
        '0x0300': {'counter': 0, 'frames': {}}
    }
    
    current_frame = None
    current_pid = None
    all_frames = []
    current_gps = {}
    last_300_frame = None  # Храним последний фрейм 0x0300
    gps_packet_number = None  # Номер пакета с GPS данными
    
    with open(file_path, 'r') as file:
        lines = file.readlines()
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Собираем GPS данные
            if 'GPSDateTime' in line:
                current_gps['datetime'] = line.split('=')[1].strip()
            elif 'GPSLatitude' in line:
                current_gps['latitude'] = float(line.split('=')[1])
            elif 'GPSLongitude' in line:
                current_gps['longitude'] = float(line.split('=')[1])
            elif 'GPSSpeed' in line:
                current_gps['speed'] = float(line.split('=')[1])
            elif 'GPSTrack' in line:
                current_gps['track'] = float(line.split('=')[1])
                
                # Сохраняем номер пакета, где найдены GPS данные
                gps_packet_number = packet_num
                
                # Если есть последний фрейм 0x0300, присваиваем ему GPS
                if last_300_frame:
                    last_300_frame['gps'] = current_gps.copy()
                    last_300_frame['gps_packet'] = gps_packet_number  # Добавляем номер пакета
                current_gps = {}
                gps_packet_number = None

            # Ищем начало транспортного пакета
            if 'Transport packet' in line:
                try:
                    packet_num = int(line.split()[2].strip(':'))
                except ValueError:
                    i += 1
                    continue
                    
                if i + 2 >= len(lines):
                    break
                    
                pid_line = lines[i+1].strip()
                start_flag_line = lines[i+2].strip()
                pid = None
                
                for key in pid_frames.keys():
                    if f'Packet ID:  {key}' in pid_line:
                        pid = key
                        break
                        
                if 'Start Flag: Yes' in start_flag_line and pid:
                    pid_frames[pid]['counter'] += 1
                    frame_number = pid_frames[pid]['counter']
                    
                    current_frame = {
                        'number': frame_number,
                        'pid': pid,
                        'start_packet': packet_num,
                        'packets': [packet_num],
                        'gps': {},  # Инициализируем пустой словарь
                        'gps_packet': None  # Инициализируем значение
                    }
                    
                    if pid == '0x0300':
                        last_300_frame = current_frame  # Сохраняем для следующего GPS
                    
                    current_pid = pid
                    pid_frames[pid]['frames'][frame_number] = current_frame
                    all_frames.append(current_frame)
                            
                elif current_frame and current_pid:
                    pid_frames[current_pid]['frames'][current_frame['number']]['packets'].append(packet_num)
            
            i += 1
            
    return pid_frames, all_frames

def decimal_to_dms(decimal):
    """Преобразует десятичные градусы в формат градусы-минуты-секунды"""
    degrees = int(decimal)
    minutes_float = (decimal - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 2)
    return degrees, minutes, seconds

def generate_report(pid_frames, all_frames):
    # Добавляем переменные для расчёта пути
    total_distance = 0.0  # Общий пройденный путь
    prev_coords = None    # Предыдущие координаты

#    print("\nПолная последовательность фреймов:")
    neighbor_frames = {    
    'prev': [],            
    'next': []             
    }
    for frame in all_frames:
        first_packet = frame['packets'][0]
        last_packet = frame['packets'][-1]
        packet_count = len(frame['packets'])
            
        if frame['pid'] == '0x0300':
            gps = frame.get('gps', {})
            latitude = gps.get('latitude')
            longitude = gps.get('longitude')
            
            if latitude is not None and longitude is not None:
                current_coords = (latitude, longitude)
                
                if prev_coords:
                    # Разворачиваем кортежи и передаем все 4 аргумента
                    distance = haversine(prev_coords[0], prev_coords[1], current_coords[0], current_coords[1])
                    total_distance += distance

                prev_coords = current_coords

            neighbor_frames['prev'] = []
            neighbor_frames['next'] = []
                
            index = all_frames.index(frame)
            prev_index = index - 1
            while len(neighbor_frames['prev']) < 3 and prev_index >= 0:
                if all_frames[prev_index]['pid'] != '0x0300':
                    neighbor_frames['prev'].append(all_frames[prev_index])
                prev_index -= 1
                
            next_index = index + 1
            while len(neighbor_frames['next']) < 3 and next_index < len(all_frames):
                if all_frames[next_index]['pid'] != '0x0300':
                    neighbor_frames['next'].append(all_frames[next_index])
                next_index += 1
                
            # Выводим предыдущие фреймы в обратном порядке
            print("\nПредыдущие фреймы:")
            for prev_frame in reversed(neighbor_frames['prev']):
                print(f"PID {prev_frame['pid']} №{prev_frame['number']}, "
                      f"пакетов: {len(prev_frame['packets'])}, "
                      f"пакеты с {prev_frame['packets'][0]} по {prev_frame['packets'][-1]}")
                
            # Выводим 0x0300 фрейм
            gps = frame.get('gps', {})
            gps_packet = frame.get('gps_packet', 'N/A')
                
            # Конвертируем время в MSK                     
            datetime_str = gps.get('datetime', 'N/A')      
            if datetime_str != 'N/A':                      
                datetime_str = convert_to_msk(datetime_str)

            print(f"\nФрейм 0x0300 №{frame['number']}, "
                  f"пакетов: {packet_count}, "
                  f"пакеты с {first_packet} по {last_packet}")
                
            print(f"GPS данные (пакет {gps_packet}):")
            print(f"Дата/Время: {datetime_str}")
            
            # Обработка широты
            latitude = gps.get('latitude', 'N/A')
            if latitude != 'N/A':
                lat_dms = decimal_to_dms(latitude)
                lat_sign = 'N' if latitude >= 0 else 'S'
                print(f"Широта:  {round(latitude, 8)} ({lat_dms[0]}° {lat_dms[1]}' {lat_dms[2]}\" {lat_sign})")
            else:
                print("Широта:  N/A")
            
            # Обработка долготы
            longitude = gps.get('longitude', 'N/A')
            if longitude != 'N/A':
                lon_dms = decimal_to_dms(longitude)
                lon_sign = 'E' if longitude >= 0 else 'W'
                print(f"Долгота: {round(longitude, 8)} ({lon_dms[0]}° {lon_dms[1]}' {lon_dms[2]}\" {lon_sign})")
            else:
                print("Долгота: N/A")
                
            print(f"Скорость: {round(gps.get('speed', 'N/A'), 1)} км/ч")
            print(f"Направление: {round(gps.get('track', 'N/A'), 1)}°")
            print(f"Пройденный путь: {total_distance:.2f} м")
                
            # Выводим следующие фреймы
            print("\nСледующие фреймы:")
            for next_frame in neighbor_frames['next']:
                print(f"PID {next_frame['pid']} №{next_frame['number']}, "
                      f"пакетов: {len(next_frame['packets'])}, "
                      f"пакеты с {next_frame['packets'][0]} по {next_frame['packets'][-1]}")

    print("\nСтатистика по PID:")
    for pid, data in pid_frames.items():
        print(f"\nPID {pid}:")
        print(f"Всего фреймов: {data['counter']}")

def generate_ssa_report(output_path, pid_frames, all_frames):
    total_distance = 0.0  # Общий пройденный путь
    prev_coords = None    # Предыдущие координаты
    with open(output_path, 'w', encoding='utf-8') as file:
        # Заголовки SSA
        file.write("﻿[Script Info]\n")
        file.write("WrapStyle: 0\n")
        file.write("ScaledBorderAndShadow: yes\n")
        file.write("Collisions: Normal\n")
        file.write("ScriptType=v4.00\n")
        file.write("\n[V4 Styles]\n")
        file.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding\n")
        file.write("Style: Default,Arial,8,16777215,255,0,0,0,0,1,1,2,1,3,10,30,0,1\n")
        file.write("\n[Events]\n")
        file.write("Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        timestamp = 0

        for frame in all_frames:
            if frame['pid'] == '0x0300':
                gps = frame.get('gps', {})
                latitude = gps.get('latitude')
                longitude = gps.get('longitude')
                
                if latitude is not None and longitude is not None:
                    current_coords = (latitude, longitude)
                    
                    if prev_coords:
                        # Разворачиваем кортежи и передаем все 4 аргумента
                        distance = haversine(prev_coords[0], prev_coords[1], current_coords[0], current_coords[1])
                        total_distance += distance
                    
                    prev_coords = current_coords
                    
                # Собираем соседние фреймы
                neighbor_frames = {'prev': [], 'next': []}
                index = all_frames.index(frame)
                
                # Предыдущие фреймы
                prev_index = index - 1
                while len(neighbor_frames['prev']) < 3 and prev_index >= 0:
                    if all_frames[prev_index]['pid'] != '0x0300':
                        neighbor_frames['prev'].append(all_frames[prev_index])
                    prev_index -= 1
                
                # Следующие фреймы
                next_index = index + 1
                while len(neighbor_frames['next']) < 3 and next_index < len(all_frames):
                    if all_frames[next_index]['pid'] != '0x0300':
                        neighbor_frames['next'].append(all_frames[next_index])
                    next_index += 1

                # Формируем текст для SSA
                text_lines = []
                
                # Предыдущие фреймы
                text_lines.append("Предыдущие фреймы:")
                for prev_frame in reversed(neighbor_frames['prev']):
                    text_lines.append(f"PID {prev_frame['pid']} №{prev_frame['number']}, "
                                     f"пакетов: {len(prev_frame['packets'])}, "
                                     f"пакеты с {prev_frame['packets'][0]} по {prev_frame['packets'][-1]}")
                
                # Основной фрейм
                gps = frame.get('gps', {})
                gps_packet = frame.get('gps_packet', 'N/A')
                
                # Конвертируем время в MSK
                datetime_str = gps.get('datetime', 'N/A')
                if datetime_str != 'N/A':
                    datetime_str = convert_to_msk(datetime_str)

                text_lines.append(f"\\NОсновной фрейм 0x0300:")
                text_lines.append(f"Номер: {frame['number']}, "
                                 f"пакетов: {len(frame['packets'])}, "
                                 f"пакеты с {frame['packets'][0]} по {frame['packets'][-1]}")
                
                text_lines.append(f"GPS данные (пакет {gps_packet}):")
                text_lines.append(f"Дата/Время: {datetime_str}")
                
                # Обработка широты
                latitude = gps.get('latitude', 'N/A')
                if latitude != 'N/A':
                    lat_dms = decimal_to_dms(latitude)
                    lat_sign = 'N' if latitude >= 0 else 'S'
                    text_lines.append(f"Широта:  {round(latitude, 8)} ({lat_dms[0]}° {lat_dms[1]}' {lat_dms[2]}\" {lat_sign})")
                else:
                    text_lines.append("Широта:  N/A")
                
                # Обработка долготы
                longitude = gps.get('longitude', 'N/A')
                if longitude != 'N/A':
                    lon_dms = decimal_to_dms(longitude)
                    lon_sign = 'E' if longitude >= 0 else 'W'
                    text_lines.append(f"Долгота: {round(longitude, 8)} ({lon_dms[0]}° {lon_dms[1]}' {lon_dms[2]}\" {lon_sign})")
                else:
                    text_lines.append("Долгота: N/A")
                
                text_lines.append(f"Скорость: {round(gps.get('speed', 'N/A'), 1)} км/ч")
                text_lines.append(f"Направление: {round(gps.get('track', 'N/A'), 1)}°")
                text_lines.append(f"Пройденный путь: {total_distance:.2f} м")
                
                # Следующие фреймы
                text_lines.append("\\NСледующие фреймы:")
                for next_frame in neighbor_frames['next']:
                    text_lines.append(f"PID {next_frame['pid']} №{next_frame['number']}, "
                                     f"пакетов: {len(next_frame['packets'])}, "
                                     f"пакеты с {next_frame['packets'][0]} по {next_frame['packets'][-1]}")

                # Записываем все строки в один субтитр длительностью 1 секунда
                start_time = f"00:00:{timestamp:02d}.00"
                end_time = f"00:00:{timestamp:02d}.99"
                full_text = '\\N'.join(text_lines)  # Разделитель строк в SSA
                file.write(f"Dialogue: 0,{start_time},{end_time},Default,NTP,0000,0000,0000,,{full_text}\n")
                
                timestamp += 1  # Переходим к следующей секунде

def generate_full_text_report(output_path, all_frames):
    with open(output_path, 'w', encoding='utf-8') as file:
        file.write("ПОЛНЫЙ ТЕКСТОВЫЙ ОТЧЁТ\n")
        file.write("======================\n")
        
        for frame in all_frames:
            gps = frame.get('gps', {})
            gps_packet = frame.get('gps_packet', 'N/A')
            
            # Формируем базовую строку без GPS
            base_info = (
                f"Фр.{frame['number']} PID:{frame['pid']} "
                f"Пакеты:{len(frame['packets'])}({frame['packets'][0]}-{frame['packets'][-1]}) "
            )
            
            # Добавляем GPS-информацию только если она есть
            if gps:
                datetime_str = gps.get('datetime', 'N/A')
                if datetime_str != 'N/A':
                    datetime_str = convert_to_msk(datetime_str)
                    
                latitude = gps.get('latitude', 'N/A')
                longitude = gps.get('longitude', 'N/A')
                speed = gps.get('speed', 'N/A')
                track = gps.get('track', 'N/A')
                
                gps_info = (
                    f"GPS:{gps_packet} "
                    f"DT:{datetime_str} "
                    f"Lat:{latitude} "
                    f"Lon:{longitude} "
                    f"Spd:{speed}км/ч "
                    f"Trk:{track}° "
                )
            else:
                gps_info = ""
            
            # Записываем итоговую строку
            file.write(base_info + gps_info + "\n")

    print(f"Полный отчёт сохранён в {output_path}")

import argparse
import os
import traceback
from datetime import datetime, timedelta, timezone
import math

def main():
    # Создаем парсер аргументов
    parser = argparse.ArgumentParser(
        description="Программа для создания SSA отчета из GPS данных"
    )
    
    # Добавляем обязательные аргументы
    parser.add_argument(
        '-i', 
        '--input', 
        required=True, 
        help='Входной файл с данными'
    )
    parser.add_argument(
        '-o', 
        '--output', 
        required=True, 
        help='Выходной SSA файл'
    )
    
    # Добавляем дополнительные параметры
    parser.add_argument(
        '--verbose', 
        action='store_true', 
        help='Подробный вывод отладочной информации'
    )
    
    # Парсим аргументы
    args = parser.parse_args()
    
    try:
        # Получаем пути из аргументов
        file_path = args.input
        ssa_output = args.output
        
        # Проверяем существование входного файла
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл {file_path} не найден")
            
        print("\nНачинаем обработку данных...")
        
        # Парсим данные
        pid_frames, all_frames = parse_packet_report(file_path)
        
        # Проверяем результат парсинга
        if not pid_frames or not all_frames:
            raise ValueError("Не удалось получить данные из файла")
            
        # Проверяем наличие фреймов
        frame_count = sum(len(record['frames']) for record in pid_frames.values())
        if frame_count == 0:
            raise ValueError("Не обнаружено фреймов в данных")
            
        # Проверяем GPS данные
        gps_records = [frame for frame in all_frames if frame['gps']]
        if not gps_records:
            print("Предупреждение: GPS данные отсутствуют")
            
        # Генерируем отчеты
#        print("\nСоздаем текстовый отчет...")
        generate_report(pid_frames, all_frames)
        
        print("\nСоздаем SSA файл...")
        generate_ssa_report(ssa_output, pid_frames, all_frames)
        
        # Добавляем генерацию полного текстового отчёта
        print("\nСоздаем полный текстовый отчёт...")
        full_text_output = os.path.splitext(args.output)[0] + "_full.txt"
        generate_full_text_report(full_text_output, all_frames)

        # Обновляем вывод итогов
        print(f"\nОбработка завершена успешно:")
        print(f"  Входной файл: {file_path}")
        print(f"  SSA файл: {ssa_output}")
        print(f"  Полный текстовый отчёт: {full_text_output}")
        print(f"  Обработано PID фреймов: {len(pid_frames)}")
        print(f"  Всего фреймов: {frame_count}")
        print(f"  Фреймов с GPS: {len(gps_records)}")
        
    except FileNotFoundError as fnf:
        print(f"\nОшибка: {fnf}")
        
    except ValueError as ve:
        print(f"\nОшибка валидации данных: {ve}")
        
    except Exception as e:
        print(f"\nПроизошла ошибка: {str(e)}")
        if args.verbose:
            traceback.print_exc()

if __name__ == "__main__":
    main()
