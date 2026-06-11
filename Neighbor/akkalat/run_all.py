import subprocess
import os
from multiprocessing.pool import ThreadPool
from datetime import datetime
import concurrent.futures

exps = [
  


]

output_dir = ""


def run_exp(exp):
    try:
        cwd = os.getcwd()
        file_name = f'{output_dir}/{exp[0]}_{exp[1]}_{"_".join(exp[2])}'
        metic_file_name = file_name + '_metrics'+''
        cmd = f"{cwd}/{exp[0]}/{exp[0]} -benchmark={exp[1]} -timing " + \
            f"-num-memory-banks=16 -bandwidth=48 " + \
            f"-switch-latency=32 " + \
            f"-magic-memory-copy -report-all " + \
            " ".join(exp[2]) + " " + \
            f"-metric-file-name={metic_file_name}" + \
            f" -> {file_name}.stdout"
        print(cmd)

        out_file_name = file_name+'_out.stdout'

        out_file = open(out_file_name, "w")
        out_file.write(f'Executing {cmd}\n')
        start_time = datetime.now()
        out_file.write(f'Start time: {start_time}\n')
        out_file.flush()

        process = subprocess.Popen(
            cmd, shell=True, stdout=out_file, stderr=out_file, cwd=cwd)
        process.wait()

        end_time = datetime.now()
        out_file.write(f'End time: {end_time}\n')

        elapsed_time = end_time - start_time
        out_file.write(f'Elapsed time: {elapsed_time}\n')

        if process.returncode != 0:
            print("Error executing ", cmd)
        else:
            print("Executed ", cmd, ", time ", elapsed_time)

        out_file.close()

    except Exception as e:
        print(e)


def create_output_dir():
    global output_dir
    output_dir = f'results/{datetime.now().strftime("%Y-%m-%d-%H-%M-%S-HugePage")}'
    # output_dir = f'results/{datetime.now().strftime("L2GMMU_Enough_PTWs")}'
    if not os.path.exists('results'):
        os.makedirs('results')

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)


def main():
    cwd = os.getcwd()

    create_output_dir()

    process = subprocess.Popen("cd 64CUPerGPU_withCache && go build", shell=True, cwd=cwd)
    process.wait()

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        future = [executor.submit(run_exp, exp) for exp in exps]

        for future in concurrent.futures.as_completed(future):
            print(future.result())

    tp.close()
    tp.join()


if __name__ == "__main__":
    main()
